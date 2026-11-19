"""Semantic validation: rules that span nodes, edges and files, beyond what JSON Schema covers."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from intensify.spec import yamlio
from intensify.spec.diagnostics import Diagnostic, Severity
from intensify.spec.loader import EdgeEntry, LoadedSpec, NodeEntry, load
from intensify.spec.models import EdgeType, LifecycleState, NodeKind

KIND_RANK: dict[NodeKind, int] = {kind: rank for rank, kind in enumerate(NodeKind)}
"""Coarse-to-fine order of node kinds, as declared in the schema."""

CODE_KINDS = frozenset({NodeKind.module, NodeKind.class_, NodeKind.function, NodeKind.block})
"""Kinds that correspond to a place in source code, so need a location once active."""


def load_and_validate(path: Path) -> LoadedSpec:
    """Load a spec and run every check. Inspect ``.diagnostics`` / ``.ok`` on the result."""
    loaded = load(path)
    if loaded.ok:
        loaded.diagnostics.extend(validate(loaded))
    return loaded


def validate(spec: LoadedSpec) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    by_id = _check_unique_ids(spec.nodes, out)
    _check_parents(spec.nodes, by_id, out)
    _check_lifecycle(spec.nodes, out)
    _check_invariants(spec.nodes, out)
    own_project = spec.document.project.id if spec.document else None
    _check_edges(spec.edges, by_id, own_project, out)
    return sorted(out, key=lambda d: (str(d.file), d.line or 0, d.code))


def _node_diag(entry: NodeEntry, code: str, message: str, *field: str | int) -> Diagnostic:
    path: tuple[str | int, ...] = ("nodes", entry.index, *field)
    return Diagnostic(
        Severity.error,
        code,
        message,
        entry.file.path,
        path,
        yamlio.line_of(entry.file.data, path),
    )


def _edge_diag(entry: EdgeEntry, code: str, message: str, *field: str) -> Diagnostic:
    path: tuple[str | int, ...] = ("edges", entry.index, *field)
    return Diagnostic(
        Severity.error,
        code,
        message,
        entry.file.path,
        path,
        yamlio.line_of(entry.file.data, path),
    )


def _check_unique_ids(nodes: list[NodeEntry], out: list[Diagnostic]) -> dict[str, NodeEntry]:
    by_id: dict[str, NodeEntry] = {}
    for entry in nodes:
        first = by_id.get(entry.node.id)
        if first is None:
            by_id[entry.node.id] = entry
            continue
        where = f"{first.file.path.name}:{yamlio.line_of(first.file.data, ('nodes', first.index))}"
        out.append(
            _node_diag(
                entry,
                "duplicate-node-id",
                f"node ID {entry.node.id!r} is already defined at {where}",
                "id",
            )
        )
    return by_id


def _check_parents(
    nodes: list[NodeEntry], by_id: dict[str, NodeEntry], out: list[Diagnostic]
) -> None:
    for entry in nodes:
        parent_id = entry.node.parent_id
        if parent_id is None:
            continue
        parent = by_id.get(parent_id)
        if parent is None:
            out.append(
                _node_diag(
                    entry, "unknown-parent", f"parent {parent_id!r} does not exist", "parent_id"
                )
            )
        elif KIND_RANK[entry.node.kind] < KIND_RANK[parent.node.kind]:
            out.append(
                _node_diag(
                    entry,
                    "invalid-nesting",
                    f"a {entry.node.kind} cannot be inside a {parent.node.kind} "
                    f"({parent_id!r}); parents must be the same kind or coarser",
                    "parent_id",
                )
            )

    # Containment cycles: follow parent links from each node; report each cycle once.
    reported: set[str] = set()
    for entry in nodes:
        seen: list[str] = []
        current: str | None = entry.node.id
        while current is not None and current in by_id and current not in seen:
            seen.append(current)
            current = by_id[current].node.parent_id
        if current is None or current not in seen:
            continue
        cycle = seen[seen.index(current) :]
        if reported.isdisjoint(cycle):
            reported.update(cycle)
            chain = " -> ".join([*cycle, cycle[0]])
            out.append(
                _node_diag(
                    by_id[cycle[0]],
                    "containment-cycle",
                    f"parent_id links form a cycle: {chain}",
                    "parent_id",
                )
            )


def _check_lifecycle(nodes: list[NodeEntry], out: list[Diagnostic]) -> None:
    for entry in nodes:
        node = entry.node
        has_location = node.technical is not None and node.technical.location is not None
        if (
            node.lifecycle_state is LifecycleState.active
            and node.kind in CODE_KINDS
            and not has_location
        ):
            out.append(
                _node_diag(
                    entry,
                    "missing-location",
                    f"active {node.kind} nodes need technical.location "
                    "(or set lifecycle_state to planned)",
                    "lifecycle_state",
                )
            )


def _check_invariants(nodes: list[NodeEntry], out: list[Diagnostic]) -> None:
    for entry in nodes:
        governance = entry.node.governance
        if governance is None or not governance.invariants:
            continue
        counts = Counter(inv.id for inv in governance.invariants)
        for index, inv in enumerate(governance.invariants):
            if counts[inv.id] > 1:
                out.append(
                    _node_diag(
                        entry,
                        "duplicate-invariant-id",
                        f"invariant ID {inv.id!r} is used more than once on this node",
                        "governance",
                        "invariants",
                        index,
                        "id",
                    )
                )


def _check_edges(
    edges: list[EdgeEntry],
    by_id: dict[str, NodeEntry],
    own_project: str | None,
    out: list[Diagnostic],
) -> None:
    def local_id(ref: str) -> str | None:
        """The local node ID for a ref, or None for a node in another project."""
        project, sep, node_id = ref.rpartition(":")
        if not sep:
            return ref
        return node_id if project == own_project else None

    seen: dict[tuple[str, str, EdgeType], EdgeEntry] = {}
    for entry in edges:
        edge = entry.edge
        for end, ref in (("from", edge.from_), ("to", edge.to)):
            node_id = local_id(ref)
            if node_id is not None and node_id not in by_id:
                out.append(
                    _edge_diag(entry, "unknown-edge-endpoint", f"node {ref!r} does not exist", end)
                )
        if edge.from_ == edge.to and edge.type is not EdgeType.CALLS:
            out.append(
                _edge_diag(
                    entry, "self-edge", f"a node cannot have a {edge.type} edge to itself", "to"
                )
            )
        key = (edge.from_, edge.to, edge.type)
        if key in seen:
            out.append(
                _edge_diag(
                    entry,
                    "duplicate-edge",
                    f"duplicate {edge.type} edge from {edge.from_!r} to {edge.to!r}",
                )
            )
        else:
            seen[key] = entry
