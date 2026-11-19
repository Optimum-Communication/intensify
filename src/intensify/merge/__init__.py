"""Merge analysis results into a loaded spec, in place, without touching human-owned fields.

Field ownership (see spec.md, "Field ownership"):

* ``id``, ``intent``, ``governance`` and non-analyzed edges are never changed.
* ``technical`` is always refreshed from code.
* ``contract`` and ``parent_id`` are refreshed only on nodes with ``provenance: analyzed``.
* ``lifecycle_state`` only moves ``planned`` -> ``in_progress``, or -> ``deprecated`` with prune.
* Nodes are never deleted.
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from intensify.parser.base import AnalysisResult, Dependency, Symbol
from intensify.spec import yamlio
from intensify.spec.constants import PLACEHOLDER_SUMMARY
from intensify.spec.loader import LoadedSpec, NodeEntry, SpecFile
from intensify.spec.models import LifecycleState, NodeKind, Provenance

SIGNATURE_SIMILARITY = 0.8
"""Minimum signature similarity for matching a renamed-and-moved symbol by name."""

_CODE_KINDS = frozenset({NodeKind.module, NodeKind.class_, NodeKind.function, NodeKind.block})

NODE_KEY_ORDER = (
    "id",
    "kind",
    "name",
    "parent_id",
    "lifecycle_state",
    "provenance",
    "intent",
    "contract",
    "technical",
    "governance",
)
"""Canonical key order. Keys the merge adds to an existing node are placed accordingly."""


@dataclass(frozen=True)
class MergeOptions:
    prune: bool = False
    """Mark nodes whose code is gone as deprecated."""
    paths: tuple[str, ...] = ()
    """Limit changes to these files or directories (POSIX, relative). Empty means everything."""

    def in_scope(self, path: str) -> bool:
        return not self.paths or any(
            path == p or path.startswith(p.rstrip("/") + "/") for p in self.paths
        )


@dataclass
class Move:
    id: str
    old: str
    new: str


@dataclass
class MergeReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    moved: list[Move] = field(default_factory=list)
    promoted: list[str] = field(default_factory=list)
    missing: list[tuple[str, str]] = field(default_factory=list)
    """(node ID, last known location) of nodes whose code is gone."""
    deprecated: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    edges_added: int = 0
    edges_removed: int = 0

    @property
    def changed(self) -> bool:
        return bool(
            self.added
            or self.updated
            or self.moved
            or self.promoted
            or self.deprecated
            or self.edges_added
            or self.edges_removed
        )


def merge(spec: LoadedSpec, analysis: AnalysisResult, options: MergeOptions) -> MergeReport:
    if spec.document is None or not spec.files:
        raise ValueError("cannot merge into a spec that failed to load")
    return _Merger(spec, analysis, options).run()


class _Merger:
    def __init__(self, spec: LoadedSpec, analysis: AnalysisResult, options: MergeOptions) -> None:
        self.spec = spec
        self.analysis = analysis
        self.options = options
        self.report = MergeReport()
        self.root_file = next(f for f in spec.files if f.is_root)
        self.symbols = {s.id: s for s in analysis.symbols}
        self.entries = {e.node.id: e for e in spec.nodes}
        self.maps: dict[str, CommentedMap] = {
            e.node.id: e.file.data["nodes"][e.index] for e in spec.nodes
        }
        self.file_of: dict[str, SpecFile] = {e.node.id: e.file for e in spec.nodes}
        self.parent_of: dict[str, str | None] = {e.node.id: e.node.parent_id for e in spec.nodes}
        self.matches: dict[str, str] = {}
        """Analyzed symbol ID -> existing node ID."""
        self.final: dict[str, str] = {}
        """Analyzed symbol ID -> node ID in the merged spec."""
        self.new: list[Symbol] = []

    def run(self) -> MergeReport:
        root_id = self._ensure_root()
        self._match()
        self.final.update(self.matches)
        self._assign_new_ids()
        self._update_matched()
        self._create_new(root_id)
        self._mark_missing()
        self._merge_edges()
        return self.report

    # -- root ----------------------------------------------------------------------------

    def _ensure_root(self) -> str:
        document = self.spec.document
        assert document is not None
        repos = [
            e for e in self.spec.nodes if e.node.kind is NodeKind.repo and not e.node.parent_id
        ]
        preferred = next((e for e in repos if e.node.id == document.project.id), None)
        if root := preferred or (repos[0] if repos else None):
            return root.node.id
        root_id = document.project.id
        node = CommentedMap()
        node["id"] = root_id
        node["kind"] = NodeKind.repo.value
        node["lifecycle_state"] = LifecycleState.active.value
        node["intent"] = CommentedMap(summary=DoubleQuotedScalarString(PLACEHOLDER_SUMMARY))
        self._nodes_seq(self.root_file).insert(0, node)
        self._register(root_id, node, self.root_file, None)
        self.report.added.append(root_id)
        return root_id

    # -- matching ------------------------------------------------------------------------

    def _match(self) -> None:
        unmatched_nodes = {e.node.id: e for e in self.spec.nodes if e.node.kind in _CODE_KINDS}
        claimed: set[str] = set()

        def take(sym: Symbol, node_id: str) -> None:
            self.matches[sym.id] = node_id
            claimed.add(node_id)
            location = _location(unmatched_nodes[node_id])
            if location is not None and location != (sym.path, sym.symbol):
                self.report.moved.append(
                    Move(
                        node_id,
                        _format_location(location),
                        _format_location((sym.path, sym.symbol)),
                    )
                )

        # 1. Exact location.
        by_location = {
            loc: e.node.id for e in unmatched_nodes.values() if (loc := _location(e)) is not None
        }
        analyzed_locations = {(s.path, s.symbol) for s in self.analysis.symbols}
        for sym in self.analysis.symbols:
            node_id = by_location.get((sym.path, sym.symbol))
            if node_id is not None and node_id not in claimed:
                take(sym, node_id)

        def stale(entry: NodeEntry) -> bool:
            """No code is at the node's recorded location any more (or it has none)."""
            location = _location(entry)
            return location is None or location not in analyzed_locations

        def open_symbols() -> list[Symbol]:
            return [s for s in self.analysis.symbols if s.id not in self.matches]

        def open_nodes() -> list[NodeEntry]:
            return [e for i, e in unmatched_nodes.items() if i not in claimed and stale(e)]

        # 2. Same ID: a planned node that has now been written, or code moved within its ID.
        for sym in open_symbols():
            entry = unmatched_nodes.get(sym.id)
            if (
                entry is not None
                and sym.id not in claimed
                and stale(entry)
                and entry.node.kind is sym.kind
            ):
                take(sym, sym.id)

        # 3. Same structure (fingerprint): moved or renamed without changes.
        by_fp: dict[str, list[Symbol]] = defaultdict(list)
        for sym in open_symbols():
            if sym.fingerprint:
                by_fp[sym.fingerprint].append(sym)
        nodes_by_fp: dict[str, list[NodeEntry]] = defaultdict(list)
        for entry in open_nodes():
            tech = entry.node.technical
            if tech is not None and tech.fingerprint:
                nodes_by_fp[tech.fingerprint].append(entry)
        for fp, entries in nodes_by_fp.items():
            syms = by_fp.get(fp, [])
            if len(entries) == 1 and len(syms) == 1 and syms[0].kind is entries[0].node.kind:
                take(syms[0], entries[0].node.id)

        # 4. Same name and kind, similar signature: moved and edited.
        for entry in open_nodes():
            location = _location(entry)
            if location is None or entry.node.technical is None:
                continue
            name = (location[1] or location[0]).rsplit(".", 1)[-1]
            signature = entry.node.technical.signature or ""
            candidates = [
                s
                for s in open_symbols()
                if s.kind is entry.node.kind
                and s.name == name
                and _similar(signature, s.signature or "") >= SIGNATURE_SIMILARITY
            ]
            if len(candidates) == 1:
                take(candidates[0], entry.node.id)
            elif len(candidates) > 1:
                places = ", ".join(_format_location((s.path, s.symbol)) for s in candidates)
                self.report.ambiguous.append(
                    f"{entry.node.id}: could be any of {places}; not matched"
                )

    # -- updates -------------------------------------------------------------------------

    def _update_matched(self) -> None:
        for sym_id, node_id in self.matches.items():
            sym = self.symbols[sym_id]
            if not self.options.in_scope(sym.path):
                continue
            entry = self.entries[node_id]
            node = self.maps[node_id]
            changed = _set_mapping(node, "technical", sym.technical())

            if entry.node.provenance is Provenance.analyzed:
                changed |= _set_mapping(node, "contract", sym.contract)
                parent = self._final_parent(sym)
                if parent is not None and parent != entry.node.parent_id:
                    _put(node, "parent_id", parent)
                    self.parent_of[node_id] = parent
                    changed = True

            if entry.node.lifecycle_state is LifecycleState.planned:
                _put(node, "lifecycle_state", LifecycleState.in_progress.value)
                self.report.promoted.append(node_id)
            elif changed and node_id not in {m.id for m in self.report.moved}:
                self.report.updated.append(node_id)

    def _final_parent(self, sym: Symbol) -> str | None:
        current = sym.parent
        while current is not None:
            if current in self.final:
                return self.final[current]
            current = self.symbols[current].parent if current in self.symbols else None
        return None

    # -- new nodes -----------------------------------------------------------------------

    def _assign_new_ids(self) -> None:
        """Pick IDs for symbols that become new nodes, before any parent links are computed."""
        self.new = [
            s
            for s in self.analysis.symbols
            if s.emit and s.id not in self.matches and self.options.in_scope(s.path)
        ]
        taken = set(self.maps)
        for sym in self.new:
            node_id, n = sym.id, 1
            while node_id in taken:
                n += 1
                node_id = f"{sym.id}-{n}"
            taken.add(node_id)
            self.final[sym.id] = node_id

    def _create_new(self, root_id: str) -> None:
        # Parent-first order, keeping each file's symbols in source order.
        new_sym_ids = {s.id for s in self.new}
        children: dict[str | None, list[Symbol]] = defaultdict(list)
        for sym in sorted(self.new, key=lambda s: (s.path, s.line)):
            parent = sym.parent if sym.parent in new_sym_ids else None
            children[parent].append(sym)
        ordered: list[Symbol] = []
        stack = list(reversed(children[None]))
        while stack:
            sym = stack.pop()
            ordered.append(sym)
            stack.extend(reversed(children[sym.id]))

        # Each new node goes after the end of its parent's subtree. New nodes under a new parent
        # share the parent's anchor; DFS order keeps them right after it. Insert bottom-up so
        # earlier indexes stay valid.
        ends = {id(f): self._subtree_ends(f) for f in self.spec.files}
        new_node_ids = {self.final[i] for i in new_sym_ids}
        anchor_of: dict[str, tuple[SpecFile, int]] = {}
        groups: dict[tuple[int, int], list[CommentedMap]] = defaultdict(list)
        for sym in ordered:
            node_id = self.final[sym.id]
            parent = self._final_parent(sym) or root_id
            if parent in new_node_ids:
                file, index = anchor_of[parent]
            else:
                file = self.file_of.get(parent, self.root_file)
                index = ends[id(file)].get(parent, len(self._nodes_seq(file)) - 1)
            anchor_of[node_id] = (file, index)
            node = _new_node(node_id, parent, sym)
            self._register(node_id, node, file, parent)
            groups[(id(file), index)].append(node)
            self.report.added.append(node_id)

        for file in self.spec.files:
            seq = self._nodes_seq(file)
            for key in sorted((k for k in groups if k[0] == id(file)), reverse=True):
                for offset, node in enumerate(groups[key]):
                    seq.insert(key[1] + 1 + offset, node)

    def _subtree_ends(self, file: SpecFile) -> dict[str, int]:
        """For each node ID, the index of the last node of its subtree in ``file``'s list."""
        ends: dict[str, int] = {}
        for index, item in enumerate(self._nodes_seq(file)):
            current: str | None = str(item.get("id"))
            seen: set[str] = set()
            while current is not None and current not in seen:
                seen.add(current)
                ends[current] = index
                current = self.parent_of.get(current)
        return ends

    # -- missing code --------------------------------------------------------------------

    def _mark_missing(self) -> None:
        matched = set(self.matches.values())
        for entry in self.spec.nodes:
            node = entry.node
            location = _location(entry)
            if node.id in matched or location is None or node.kind not in _CODE_KINDS:
                continue
            path = location[0]
            if not self.options.in_scope(path):
                continue
            file_exists = (self.spec.path.parent / path).is_file()
            if file_exists and path not in self.analysis.files:
                continue  # a file we don't analyze (unsupported language or excluded)
            if node.lifecycle_state is LifecycleState.deprecated:
                continue
            self.report.missing.append((node.id, _format_location(location)))
            if self.options.prune:
                _put(self.maps[node.id], "lifecycle_state", LifecycleState.deprecated.value)
                self.report.deprecated.append(node.id)

    # -- edges ---------------------------------------------------------------------------

    def _merge_edges(self) -> None:
        desired: dict[tuple[str, str, str], Dependency] = {}
        for dep in self.analysis.dependencies:
            source_sym = self.symbols.get(dep.source)
            if source_sym is None or not self.options.in_scope(source_sym.path):
                continue
            source, target = self._endpoint(dep.source), self._endpoint(dep.target)
            if source is None or target is None or source == target:
                continue
            if self._is_ancestor(source, target) or self._is_ancestor(target, source):
                continue
            desired.setdefault((source, target, dep.type.value), dep)

        existing: set[tuple[str, str, str]] = set()
        stale: list[tuple[SpecFile, CommentedMap]] = []
        for entry in self.spec.edges:
            edge = entry.edge
            key = (edge.from_, edge.to, edge.type.value)
            existing.add(key)
            if edge.provenance is not Provenance.analyzed or key in desired:
                continue
            if self._source_in_scope(edge.from_):
                stale.append((entry.file, entry.file.data["edges"][entry.index]))

        for file, item in stale:
            seq = file.data["edges"]
            for index in range(len(seq)):
                if seq[index] is item:
                    del seq[index]
                    break
            self.report.edges_removed += 1

        additions: dict[int, list[CommentedMap]] = defaultdict(list)
        files: dict[int, SpecFile] = {}
        for key in sorted(k for k in desired if k not in existing):
            file = self.file_of.get(key[0], self.root_file)
            files[id(file)] = file
            additions[id(file)].append(_new_edge(*key, desired[key]))
        for file_key, edges in additions.items():
            seq = self._edges_seq(files[file_key])
            seq.extend(edges)
            self.report.edges_added += len(edges)

    def _endpoint(self, sym_id: str) -> str | None:
        """The node an edge to ``sym_id`` attaches to: itself, or an ancestor in the same file."""
        start = self.symbols.get(sym_id)
        current: str | None = sym_id
        while current is not None and start is not None:
            sym = self.symbols.get(current)
            if sym is None or sym.path != start.path:
                return None
            if current in self.final:
                return self.final[current]
            current = sym.parent
        return None

    def _is_ancestor(self, ancestor: str, node: str) -> bool:
        current = self.parent_of.get(node)
        seen: set[str] = set()
        while current is not None and current not in seen:
            if current == ancestor:
                return True
            seen.add(current)
            current = self.parent_of.get(current)
        return False

    def _source_in_scope(self, node_id: str) -> bool:
        if not self.options.paths:
            return True
        entry = self.entries.get(node_id)
        location = _location(entry) if entry is not None else None
        return location is not None and self.options.in_scope(location[0])

    # -- YAML helpers --------------------------------------------------------------------

    def _register(
        self, node_id: str, node: CommentedMap, file: SpecFile, parent: str | None
    ) -> None:
        self.maps[node_id] = node
        self.file_of[node_id] = file
        self.parent_of[node_id] = parent

    @staticmethod
    def _nodes_seq(file: SpecFile) -> CommentedSeq:
        if file.data.get("nodes") is None:
            file.data["nodes"] = CommentedSeq()
        seq: CommentedSeq = file.data["nodes"]
        return seq

    @staticmethod
    def _edges_seq(file: SpecFile) -> CommentedSeq:
        if file.data.get("edges") is None:
            file.data["edges"] = CommentedSeq()
        seq: CommentedSeq = file.data["edges"]
        return seq


def _location(entry: NodeEntry) -> tuple[str, str | None] | None:
    tech = entry.node.technical
    if tech is None or tech.location is None:
        return None
    return (tech.location.path, tech.location.symbol)


def _format_location(location: tuple[str, str | None]) -> str:
    path, symbol = location
    return f"{path}:{symbol}" if symbol else path


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _rt(value: Any) -> Any:
    """Plain data -> round-trip YAML data, using flow style for small flat mappings."""
    if isinstance(value, dict):
        out = CommentedMap()
        for k, v in value.items():
            out[k] = _rt(v)
        if all(not isinstance(v, dict | list) for v in value.values()):
            out.fa.set_flow_style()
        return out
    if isinstance(value, list):
        seq = CommentedSeq(_rt(v) for v in value)
        return seq
    return value


def _set_mapping(node: CommentedMap, key: str, value: dict[str, Any] | None) -> bool:
    """Make ``node[key]`` equal ``value`` (removing it for None), editing in place when present.

    Unchanged sub-values are left alone so their formatting and comments survive.
    """
    current = node.get(key)
    if value is None:
        if current is None:
            return False
        del node[key]
        return True
    if current is None or not isinstance(current, dict):
        _put(node, key, _rt(value))
        return True
    changed = False
    for k in [k for k in current if k not in value]:
        del current[k]
        changed = True
    for k, v in value.items():
        if yamlio.to_plain(current.get(k)) != v:
            current[k] = _rt(v)
            changed = True
    return changed


def _put(node: CommentedMap, key: str, value: Any) -> None:
    """Set ``node[key]``; a new key goes in its canonical position rather than at the end."""
    if key in node or key not in NODE_KEY_ORDER:
        node[key] = value
        return
    rank = NODE_KEY_ORDER.index(key)
    position = sum(1 for k in node if k in NODE_KEY_ORDER and NODE_KEY_ORDER.index(k) < rank)
    node.insert(position, key, value)


def _new_node(node_id: str, parent: str, sym: Symbol) -> CommentedMap:
    node = CommentedMap()
    node["id"] = node_id
    node["kind"] = sym.kind.value
    node["parent_id"] = parent
    node["lifecycle_state"] = LifecycleState.active.value
    node["provenance"] = Provenance.analyzed.value
    node["intent"] = CommentedMap(summary=DoubleQuotedScalarString(PLACEHOLDER_SUMMARY))
    if sym.contract:
        node["contract"] = _rt(sym.contract)
    node["technical"] = _rt(sym.technical())
    return node


def _new_edge(source: str, target: str, edge_type: str, dep: Dependency) -> CommentedMap:
    edge = CommentedMap()
    edge["from"] = source
    edge["to"] = target
    edge["type"] = edge_type
    if dep.confidence.value != "high":
        edge["confidence"] = dep.confidence.value
    edge["provenance"] = Provenance.analyzed.value
    edge.fa.set_flow_style()
    return edge
