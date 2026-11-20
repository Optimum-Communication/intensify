"""Scoped context bundles for AI coding agents working on one node.

A bundle holds what an agent needs to implement or change a node well: its intent and contract,
the architecture around it, the invariants it must respect, what it depends on and what depends
on it, its tests, and the planned work nearby that it should leave room for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from intensify.graph import UNFINISHED, Graph
from intensify.parser import symbol_span
from intensify.spec import LoadedSpec
from intensify.spec.models import EdgeType, Invariant, Node

CHARS_PER_TOKEN = 4
"""Rough token estimate used for ``budget``; good enough to keep prompts in bounds."""

MAX_EXCERPT_LINES = 200
ALWAYS_KEPT = 90
"""Sections at or above this priority (intent, contract, location) are never trimmed."""


@dataclass
class Related:
    node_id: str
    relation: str
    """How it relates to the target, e.g. ``CALLS``, ``called by``, ``sibling``, ``blocked by``."""
    node: Node | None
    """None for a node in another project."""


@dataclass
class Bundle:
    target: Node
    ancestors: list[Node] = field(default_factory=list)
    invariants: list[tuple[str, Invariant]] = field(default_factory=list)
    """(owner node ID, invariant): the target's own and those inherited from ancestors."""
    dependencies: list[Related] = field(default_factory=list)
    dependents: list[Related] = field(default_factory=list)
    future: list[Related] = field(default_factory=list)
    """Planned and in-progress work connected to the target: leave room, don't build."""
    tests: list[Node] = field(default_factory=list)
    test_command: str | None = None
    excerpts: dict[str, str] = field(default_factory=dict)
    """Node ID -> source code, when source was requested."""
    language: str | None = None
    trimmed: list[str] = field(default_factory=list)
    """Sections dropped to fit the budget."""


def build(loaded: LoadedSpec, node_id: str, *, include_source: bool = False) -> Bundle:
    graph = Graph.from_spec(loaded)
    if node_id not in graph:
        raise KeyError(node_id)
    target = graph.node(node_id)
    bundle = Bundle(target=target, ancestors=graph.ancestors(node_id))
    bundle.language = _language(target) or next(
        (lang for a in bundle.ancestors if (lang := _language(a))), None
    )

    for owner in [target, *bundle.ancestors]:
        for inv in (owner.governance.invariants or []) if owner.governance else []:
            bundle.invariants.append((owner.id, inv))

    def related(ref: str, relation: str) -> Related:
        local = graph.local(ref)
        return Related(local, relation, graph.get(local))

    bundle.dependencies = [related(e.to, _verb(e.type)) for e in graph.dependencies(node_id)]
    bundle.dependents = [
        related(e.from_, f"{_verb(e.type)} this") for e in graph.dependents(node_id)
    ]
    bundle.tests = [n for t in graph.tests_of(node_id) if (n := graph.get(t)) is not None]

    bundle.future = _future(graph, node_id)

    document = loaded.document
    if document and document.config and document.config.verify:
        bundle.test_command = document.config.verify.test_command

    if include_source:
        root = loaded.path.parent
        for node in [target, *(d.node for d in bundle.dependencies if d.node is not None)]:
            if excerpt := _excerpt(root, node):
                bundle.excerpts[node.id] = excerpt
    return bundle


def _future(graph: Graph, node_id: str) -> list[Related]:
    """Planned and in-progress work connected to the node, plus unfinished prerequisites."""
    future: dict[str, Related] = {}

    def add_future(ref: str, relation: str) -> None:
        local = graph.local(ref)
        if local != node_id and local not in future:
            future[local] = Related(local, relation, graph.get(local))

    for child in graph.descendants(node_id):
        if graph.node(child).lifecycle_state in UNFINISHED:
            add_future(child, "planned part of this node")
    for sibling in graph.siblings(node_id):
        if graph.node(sibling).lifecycle_state in UNFINISHED:
            add_future(sibling, "planned sibling")
    for edge in graph.outgoing.get(node_id, []):
        if edge.type is EdgeType.BLOCKS:
            add_future(edge.to, "blocked by this node")
        elif edge.lifecycle_state in UNFINISHED:
            add_future(edge.to, f"this will {_verb(edge.type)} it")
    for edge in graph.incoming.get(node_id, []):
        if edge.type is EdgeType.BLOCKS:
            add_future(edge.from_, "blocks this node (prerequisite)")
        elif edge.lifecycle_state in UNFINISHED:
            add_future(edge.from_, f"will {_verb(edge.type)} this node")
    return [
        f
        for f in future.values()
        if f.node is None
        or f.node.lifecycle_state in UNFINISHED
        or f.relation.startswith("blocks this")
    ]


def _verb(edge_type: EdgeType) -> str:
    """``READS_FROM`` -> ``reads from``."""
    return edge_type.value.lower().replace("_", " ")


def _language(node: Node) -> str | None:
    return node.technical.language if node.technical else None


def _location(node: Node) -> str | None:
    if node.technical is None or node.technical.location is None:
        return None
    loc = node.technical.location
    return f"{loc.path}:{loc.symbol}" if loc.symbol else loc.path


def _excerpt(root: Path, node: Node) -> str | None:
    if node.technical is None or node.technical.location is None:
        return None
    loc = node.technical.location
    span = symbol_span(root, loc.path, loc.symbol)
    if span is None:
        return None
    lines = (root / loc.path).read_text(encoding="utf-8", errors="replace").splitlines()
    start, end = span
    chosen = lines[start - 1 : min(end, start - 1 + MAX_EXCERPT_LINES)]
    if end - start + 1 > MAX_EXCERPT_LINES:
        chosen.append(f"... ({end - start + 1 - MAX_EXCERPT_LINES} more lines)")
    return "\n".join(chosen)


# -- rendering ---------------------------------------------------------------------------


def _summary_line(item: Related) -> str:
    if item.node is None:
        return f"- `{item.node_id}` ({item.relation}, in another project)"
    node = item.node
    parts = [f"- `{node.id}` ({item.relation}; {node.kind.value}, {node.lifecycle_state.value})"]
    if node.technical and node.technical.signature:
        parts.append(f"`{node.technical.signature}`")
    parts.append(f"— {node.intent.summary}")
    return " ".join(parts)


def _contract_lines(node: Node) -> list[str]:
    contract = node.contract
    if contract is None:
        return ["No contract yet. Choose a clear interface and record it in the spec."]
    lines = []
    for param in contract.inputs or []:
        optional = "" if param.required else " (optional)"
        type_text = f": `{param.type}`" if param.type else ""
        desc = f" — {param.description}" if param.description else ""
        lines.append(f"- input `{param.name}`{type_text}{optional}{desc}")
    for out in contract.outputs or []:
        label = f" `{out.name}`" if out.name else ""
        type_text = f" `{out.type}`" if out.type else ""
        desc = f" — {out.description}" if out.description else ""
        lines.append(f"- returns{label}{type_text}{desc}")
    for err in contract.errors or []:
        when = f" when {err.when}" if err.when else ""
        lines.append(f"- raises `{err.type}`{when}")
    return lines or ["Contract is empty."]


def _location_lines(bundle: Bundle) -> list[str]:
    t = bundle.target
    location = _location(t)
    if location:
        lines = [f"`{location}`"]
        if t.technical and t.technical.signature:
            lines.append(f"Current signature: `{t.technical.signature}`")
        return lines
    parent_loc = next((loc for a in bundle.ancestors if (loc := _location(a))), None)
    lines = ["Not implemented yet."]
    if parent_loc:
        lines.append(f"Its parent lives at `{parent_loc}`; put it there or alongside.")
    return lines


def _invariant_lines(bundle: Bundle) -> list[str]:
    lines = []
    for owner, inv in bundle.invariants:
        source = "" if owner == bundle.target.id else f" (from `{owner}`)"
        rule = ""
        if inv.rule is not None:
            rule = f" — checked: `{inv.rule.model_dump_json(exclude_none=True, by_alias=True)}`"
        lines.append(f"- **{inv.id}**{source}: {inv.text}{rule}")
    return lines


def _sections(bundle: Bundle) -> list[tuple[int, str, list[str]]]:
    """(priority, title, lines); higher priority survives trimming longer."""
    t = bundle.target
    out: list[tuple[int, str, list[str]]] = []

    intent = [t.intent.summary]
    if t.intent.rationale:
        intent += ["", f"**Why:** {t.intent.rationale}"]
    if t.intent.author and t.intent.author.value == "ai":
        intent += ["", "_This intent was drafted by AI and may need confirming._"]
    out.append((100, "Intent", intent))
    out.append((95, "Contract", _contract_lines(t)))

    out.append((90, "Location", _location_lines(bundle)))
    if bundle.invariants:
        out.append((85, "Invariants (must hold)", _invariant_lines(bundle)))

    if bundle.dependencies:
        out.append((70, "Depends on", [_summary_line(d) for d in bundle.dependencies]))
    if bundle.dependents:
        out.append((65, "Used by", [_summary_line(d) for d in bundle.dependents]))

    if bundle.future:
        lines = [
            "Leave room for these, but do not implement them now and do not add speculative "
            "abstractions for them:",
            "",
            *(_summary_line(f) for f in bundle.future),
        ]
        out.append((60, "Future capabilities (planned, not part of this task)", lines))

    if bundle.ancestors:
        lines = [
            f"- {a.kind.value} `{a.id}`: {a.intent.summary}" for a in reversed(bundle.ancestors)
        ]
        out.append((50, "Where this fits", lines))

    tests = [f"- `{n.id}`" + (f" ({_location(n)})" if _location(n) else "") for n in bundle.tests]
    if not tests:
        tests = ["- No tests verify this node yet. Add at least one."]
    if bundle.test_command:
        tests += ["", f"Test command: `{bundle.test_command}`"]
    out.append((45, "Tests", tests))

    for node_id, code in bundle.excerpts.items():
        label = "Current code" if node_id == t.id else f"Code of `{node_id}`"
        fence = bundle.language or ""
        priority = 30 if node_id == t.id else 20
        out.append((priority, label, [f"```{fence}", code, "```"]))

    finish = [
        "1. Implement or change the node to satisfy its intent, contract and invariants.",
        "2. Add or update tests.",
        "3. Run `intensify analyze` to record the code in the spec.",
        f"4. Run `intensify verify {t.id}`. It checks the contract, invariants and tests, then "
        "marks the node active.",
    ]
    out.append((80, "When you're done", finish))
    return out


def render_markdown(bundle: Bundle, *, budget: int | None = None) -> str:
    t = bundle.target
    title = f"# `{t.id}` ({t.kind.value}, {t.lifecycle_state.value})"
    sections = _sections(bundle)
    order = {id(s): i for i, s in enumerate(sections)}

    def assemble(chosen: list[tuple[int, str, list[str]]]) -> str:
        body = [title]
        for _, heading, lines in sorted(chosen, key=lambda s: order[id(s)]):
            body += ["", f"## {heading}", "", *lines]
        if bundle.trimmed:
            body += ["", f"_Trimmed to fit the budget: {', '.join(bundle.trimmed)}._"]
        return "\n".join(body) + "\n"

    chosen = list(sections)
    text = assemble(chosen)
    if budget is not None:
        for section in sorted(sections, key=lambda s: s[0]):
            if len(text) // CHARS_PER_TOKEN <= budget or section[0] >= ALWAYS_KEPT:
                break
            chosen.remove(section)
            bundle.trimmed.append(section[1])
            text = assemble(chosen)
    return text


def to_dict(bundle: Bundle) -> dict[str, Any]:
    def node_dict(node: Node | None) -> dict[str, Any] | None:
        return node.model_dump(mode="json", by_alias=True, exclude_none=True) if node else None

    def related(items: list[Related]) -> list[dict[str, Any]]:
        return [{"id": r.node_id, "relation": r.relation, "node": node_dict(r.node)} for r in items]

    return {
        "target": node_dict(bundle.target),
        "ancestors": [node_dict(a) for a in bundle.ancestors],
        "invariants": [
            {"owner": owner, **inv.model_dump(mode="json", by_alias=True, exclude_none=True)}
            for owner, inv in bundle.invariants
        ],
        "dependencies": related(bundle.dependencies),
        "dependents": related(bundle.dependents),
        "future": related(bundle.future),
        "tests": [node_dict(n) for n in bundle.tests],
        "test_command": bundle.test_command,
        "excerpts": bundle.excerpts,
    }
