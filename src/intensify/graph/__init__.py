"""In-memory graph over a loaded spec: lookups, containment and edge traversal."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from intensify.spec import LoadedSpec, NodeEntry, yamlio
from intensify.spec.models import Edge, EdgeType, LifecycleState, Node, NodeKind

DEPENDENCY_TYPES = frozenset(
    {EdgeType.CALLS, EdgeType.READS_FROM, EdgeType.WRITES_TO, EdgeType.IMPLEMENTS}
)
"""Edge types that mean "``from`` depends on ``to``" at runtime or by design."""

UNFINISHED = frozenset({LifecycleState.planned, LifecycleState.in_progress})


@dataclass
class Graph:
    project: str
    entries: dict[str, NodeEntry] = field(default_factory=dict)
    children: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    outgoing: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))
    incoming: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))

    @classmethod
    def from_spec(cls, loaded: LoadedSpec) -> Graph:
        """The graph of the spec as loaded (its models)."""
        project = loaded.document.project.id if loaded.document else ""
        return cls._build(project, loaded.nodes, [e.edge for e in loaded.edges])

    @classmethod
    def from_data(cls, loaded: LoadedSpec) -> Graph:
        """The graph of the spec files' current round-trip data, e.g. after an in-memory merge."""
        nodes: list[NodeEntry] = []
        edges: list[Edge] = []
        for file in loaded.files:
            for index, item in enumerate(file.data.get("nodes") or []):
                nodes.append(NodeEntry(Node.model_validate(yamlio.to_plain(item)), file, index))
            edges.extend(
                Edge.model_validate(yamlio.to_plain(e)) for e in file.data.get("edges") or []
            )
        project = loaded.document.project.id if loaded.document else ""
        return cls._build(project, nodes, edges)

    @classmethod
    def _build(cls, project: str, nodes: list[NodeEntry], edges: list[Edge]) -> Graph:
        graph = cls(project=project)
        for entry in nodes:
            graph.entries.setdefault(entry.node.id, entry)
        for entry in nodes:
            if entry.node.parent_id is not None:
                graph.children[entry.node.parent_id].append(entry.node.id)
        for edge in edges:
            graph.outgoing[graph.local(edge.from_)].append(edge)
            graph.incoming[graph.local(edge.to)].append(edge)
        return graph

    # -- lookups -------------------------------------------------------------------------

    def __contains__(self, node_id: object) -> bool:
        return node_id in self.entries

    def node(self, node_id: str) -> Node:
        return self.entries[node_id].node

    def get(self, node_id: str) -> Node | None:
        entry = self.entries.get(node_id)
        return entry.node if entry else None

    def nodes(self) -> list[Node]:
        return [e.node for e in self.entries.values()]

    def local(self, ref: str) -> str:
        """Strip this project's ``<project>:`` prefix; other projects' refs are unchanged."""
        project, sep, rest = ref.rpartition(":")
        return rest if sep and project == self.project else ref

    # -- containment ---------------------------------------------------------------------

    def ancestors(self, node_id: str) -> list[Node]:
        """Containing nodes, nearest first."""
        out: list[Node] = []
        seen = {node_id}
        current = self.get(node_id)
        while current is not None and current.parent_id and current.parent_id not in seen:
            seen.add(current.parent_id)
            current = self.get(current.parent_id)
            if current is not None:
                out.append(current)
        return out

    def descendants(self, node_id: str) -> list[str]:
        out: list[str] = []
        stack = list(reversed(self.children.get(node_id, [])))
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            out.append(current)
            stack.extend(reversed(self.children.get(current, [])))
        return out

    def siblings(self, node_id: str) -> list[str]:
        node = self.get(node_id)
        if node is None or node.parent_id is None:
            return []
        return [c for c in self.children.get(node.parent_id, []) if c != node_id]

    # -- edges ---------------------------------------------------------------------------

    def dependencies(self, node_id: str) -> list[Edge]:
        return [e for e in self.outgoing.get(node_id, []) if e.type in DEPENDENCY_TYPES]

    def dependents(self, node_id: str) -> list[Edge]:
        return [e for e in self.incoming.get(node_id, []) if e.type in DEPENDENCY_TYPES]

    def tests_of(self, node_id: str) -> list[str]:
        """Nodes with a VERIFIES edge into the node or anything inside it."""
        targets = {node_id, *self.descendants(node_id)}
        found: dict[str, None] = {}
        for target in targets:
            for edge in self.incoming.get(target, []):
                if edge.type is EdgeType.VERIFIES:
                    found[self.local(edge.from_)] = None
        return list(found)

    def blast_radius(self, node_id: str) -> list[str]:
        """Everything that transitively depends on the node, nearest first."""
        out: list[str] = []
        seen = {node_id}
        frontier = [node_id]
        while frontier:
            next_frontier = []
            for current in frontier:
                for edge in self.dependents(current):
                    source = self.local(edge.from_)
                    if source not in seen:
                        seen.add(source)
                        out.append(source)
                        next_frontier.append(source)
            frontier = next_frontier
        return out

    # -- search --------------------------------------------------------------------------

    def search(
        self,
        query: str = "",
        *,
        kind: NodeKind | None = None,
        state: LifecycleState | None = None,
        tag: str | None = None,
        limit: int | None = None,
    ) -> list[Node]:
        """Nodes matching every given filter, best text matches first.

        ``query`` matches words against the ID, name, intent and tags; ID matches rank highest.
        """
        words = query.lower().split()
        scored: list[tuple[int, str, Node]] = []
        for node in self.nodes():
            if kind is not None and node.kind is not kind:
                continue
            if state is not None and node.lifecycle_state is not state:
                continue
            tags = node.governance.tags or [] if node.governance else []
            if tag is not None and tag not in tags:
                continue
            score = 0
            if words:
                haystacks = [
                    (node.id.lower(), 3),
                    ((node.name or "").lower(), 2),
                    (node.intent.summary.lower(), 1),
                    ((node.intent.rationale or "").lower(), 1),
                    (" ".join(tags).lower(), 1),
                ]
                for word in words:
                    word_score = sum(weight for text, weight in haystacks if word in text)
                    if word_score == 0:
                        break
                    score += word_score
                else:
                    scored.append((score, node.id, node))
                continue
            scored.append((score, node.id, node))
        scored.sort(key=lambda item: (-item[0], item[1]))
        nodes = [node for _, _, node in scored]
        return nodes[:limit] if limit is not None else nodes
