"""Model Context Protocol server exposing the Intensify graph to IDE agents.

Every tool reloads the spec, so edits made elsewhere (by people, ``analyze`` or another agent) are
always seen. All logic lives in the library; tools are thin wrappers. Expected failures raise
``ToolError``, whose message reaches the agent; anything else is reported as a crash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations

from intensify import __version__
from intensify.context import build, render_markdown
from intensify.differ import detect
from intensify.differ.report import to_json
from intensify.graph import UNFINISHED, Graph
from intensify.pipeline import SpecInvalidError
from intensify.pipeline import analyze_and_write as run_analyze
from intensify.spec import LoadedSpec, load_and_validate, yamlio
from intensify.spec.edit import EditError, add_node, find, patch, save
from intensify.spec.models import LifecycleState, Node, NodeKind
from intensify.verify import NodeNotFoundError, verify

INSTRUCTIONS = """\
Intensify describes this repository's architecture and intent as a graph of nodes (repos,
modules, classes, functions) and typed edges (CALLS, READS_FROM, WRITES_TO, IMPLEMENTS,
VERIFIES, BLOCKS), stored in intensify.yaml. Planned nodes are work that hasn't been built yet.

To implement or change a node:
1. get_context(node_id): its intent, contract, invariants, dependencies, tests, and planned
   work nearby. Respect the invariants. Leave room for the planned work, but don't build it.
2. Write the code and tests.
3. analyze_code() to record the code in the spec.
4. verify_node(node_id) to check the contract, invariants and tests and mark the node active.

Use search_nodes to find nodes, list_planned for work to do, and update_intent to record what
code is for (set author "ai" so people know to review it). Never edit intensify.yaml by hand.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)


def _summary(node: Node) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": node.id,
        "kind": node.kind.value,
        "lifecycle_state": node.lifecycle_state.value,
        "summary": node.intent.summary,
    }
    if node.technical and node.technical.location:
        loc = node.technical.location
        out["location"] = f"{loc.path}:{loc.symbol}" if loc.symbol else loc.path
    return out


def create_server(spec_path: Path) -> MCPServer:  # noqa: PLR0915 - one closure per tool
    server: MCPServer = MCPServer(
        name="intensify",
        title="Intensify",
        instructions=INSTRUCTIONS,
        version=__version__,
    )

    def load() -> LoadedSpec:
        loaded = load_and_validate(spec_path)
        if not loaded.ok:
            details = "; ".join(f"{d.file.name}:{d.line}: {d.message}" for d in loaded.errors)
            raise ToolError(f"intensify.yaml is invalid: {details}")
        return loaded

    @server.tool(annotations=READ_ONLY)
    def search_nodes(
        query: str = "",
        kind: str | None = None,
        state: str | None = None,
        tag: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Find nodes by words in their ID, name or intent, filtered by kind, state or tag.

        kind: ecosystem, repo, module, class, function or block.
        state: planned, in_progress, active or deprecated.
        """
        graph = Graph.from_spec(load())
        nodes = graph.search(
            query,
            kind=NodeKind(kind) if kind else None,
            state=LifecycleState(state) if state else None,
            tag=tag,
            limit=limit,
        )
        return [_summary(n) for n in nodes]

    @server.tool(annotations=READ_ONLY)
    def get_node(node_id: str) -> dict[str, Any]:
        """Everything the spec records about one node, plus its children and edges."""
        loaded = load()
        graph = Graph.from_spec(loaded)
        node = graph.get(node_id)
        if node is None:
            raise ToolError(f"node {node_id!r} does not exist")
        entry = graph.entries[node_id]
        return {
            "node": node.model_dump(mode="json", by_alias=True, exclude_none=True),
            "file": str(entry.file.path.relative_to(loaded.path.parent)),
            "children": graph.children.get(node_id, []),
            "outgoing": [
                e.model_dump(mode="json", by_alias=True, exclude_none=True)
                for e in graph.outgoing.get(node_id, [])
            ],
            "incoming": [
                e.model_dump(mode="json", by_alias=True, exclude_none=True)
                for e in graph.incoming.get(node_id, [])
            ],
        }

    @server.tool(annotations=READ_ONLY)
    def get_context(node_id: str, budget: int | None = None, include_source: bool = True) -> str:
        """Prompt-ready Markdown context for implementing or changing a node.

        budget: approximate token limit; lower-priority sections are dropped first.
        """
        try:
            bundle = build(load(), node_id, include_source=include_source)
        except KeyError:
            raise ToolError(f"node {node_id!r} does not exist") from None
        return render_markdown(bundle, budget=budget)

    @server.tool(annotations=READ_ONLY)
    def list_planned() -> list[dict[str, Any]]:
        """Nodes that are planned or in progress: the work waiting to be done."""
        graph = Graph.from_spec(load())
        return [_summary(n) for n in graph.nodes() if n.lifecycle_state in UNFINISHED]

    @server.tool(annotations=WRITES)
    def update_intent(
        node_id: str, summary: str, rationale: str | None = None, author: str = "ai"
    ) -> str:
        """Record what a node is for (summary) and why it exists or is built this way (rationale).

        author: "ai" (the default) marks the intent as a draft for a person to review.
        """
        loaded = load()
        intent: dict[str, Any] = {"summary": summary, "author": author}
        if rationale is not None:
            intent["rationale"] = rationale
        try:
            _, node = find(loaded, node_id)
            patch(node, {"intent": intent})
            save(loaded)
        except EditError as exc:
            raise ToolError(str(exc)) from None
        return f"Updated the intent of {node_id}."

    @server.tool(annotations=WRITES)
    def add_planned_node(
        node_id: str,
        kind: str,
        summary: str,
        parent_id: str | None = None,
        rationale: str | None = None,
        contract: dict[str, Any] | None = None,
    ) -> str:
        """Add a planned node: a piece of work to build later.

        contract (optional): {"inputs": [{"name", "type"}], "outputs": [{"type"}],
        "errors": [{"type", "when"}]}.
        """
        loaded = load()
        node: dict[str, Any] = {"id": node_id, "kind": NodeKind(kind).value}
        if parent_id:
            node["parent_id"] = parent_id
        node["lifecycle_state"] = LifecycleState.planned.value
        node["intent"] = {"summary": summary, **({"rationale": rationale} if rationale else {})}
        if contract:
            node["contract"] = contract
        try:
            add_node(loaded, node)
            save(loaded)
        except EditError as exc:
            raise ToolError(str(exc)) from None
        return f"Added planned node {node_id}."

    @server.tool(annotations=WRITES)
    def analyze_code(paths: list[str] | None = None, dry_run: bool = False) -> dict[str, Any]:
        """Update intensify.yaml from the code (like `intensify analyze`).

        paths: only update nodes for these files or directories, relative to the repository.
        Never changes intent, governance or hand-written edges.
        """
        try:
            outcome = run_analyze(spec_path, paths=tuple(paths or ()), dry_run=dry_run)
        except SpecInvalidError as exc:
            raise ToolError(f"intensify.yaml is invalid ({exc})") from None
        report = outcome.report
        return {
            "added": report.added,
            "updated": report.updated,
            "moved": [{"id": m.id, "from": m.old, "to": m.new} for m in report.moved],
            "promoted": report.promoted,
            "missing": [{"id": i, "location": w} for i, w in report.missing],
            "edges_added": report.edges_added,
            "edges_removed": report.edges_removed,
            "written": not dry_run and bool(outcome.diffs),
        }

    @server.tool(annotations=WRITES)
    def verify_node(node_id: str, run_tests: bool = True, dry_run: bool = False) -> dict[str, Any]:
        """Check a node's code against its contract, invariants and tests.

        When every check passes (and dry_run is false), the node is marked active.
        """
        try:
            result = verify(spec_path, node_id, run_tests=run_tests, dry_run=dry_run)
        except SpecInvalidError as exc:
            raise ToolError(f"intensify.yaml is invalid ({exc})") from None
        except NodeNotFoundError:
            raise ToolError(f"node {node_id!r} does not exist") from None
        return {
            "verified": result.ok,
            "marked_active": result.ok and result.promoted and not dry_run,
            "checks": [
                {"name": c.name, "ok": c.ok, "skipped": c.skipped, "detail": c.detail}
                for c in result.checks
            ],
        }

    @server.tool(annotations=READ_ONLY)
    def drift_report() -> str:
        """How the code differs from the spec (JSON), like `intensify drift --format json`."""
        try:
            return to_json(detect(spec_path))
        except SpecInvalidError as exc:
            raise ToolError(f"intensify.yaml is invalid ({exc})") from None

    @server.resource(
        "intensify://spec",
        name="intensify.yaml",
        description="The root spec file.",
        mime_type="application/yaml",
    )
    def spec_resource() -> str:
        try:
            return yamlio.dumps(next(f for f in load().files if f.is_root).data)
        except ToolError as exc:
            raise ResourceError(str(exc)) from None

    return server
