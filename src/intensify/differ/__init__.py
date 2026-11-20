"""Drift detection: how the code differs from the committed spec.

Drift runs the same analysis and merge as ``intensify analyze``, in memory, and reports what the
merge would change, plus contract and invariant checks. Volatile fields (``complexity``,
``fingerprint``, ``signature``) never produce findings.
"""

from __future__ import annotations

import dataclasses
import fnmatch
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from intensify.differ.base import keys_at
from intensify.parser.base import Symbol
from intensify.pipeline import Merged, analyze_and_merge
from intensify.spec import PLACEHOLDER_SUMMARY, LoadedSpec, load, yamlio
from intensify.spec.models import (
    Contract,
    DriftCategory,
    Invariant,
    LifecycleState,
    Node,
    Provenance,
)

DEFAULT_FAIL_ON: frozenset[DriftCategory] = frozenset(
    {
        DriftCategory.undocumented_edge,
        DriftCategory.contract_mismatch,
        DriftCategory.invariant_violation,
        DriftCategory.missing_node,
    }
)
NOTE_CATEGORIES = frozenset({DriftCategory.moved_node, DriftCategory.unenriched_node})
"""Informational categories: reported as notes unless listed in ``fail_on``."""

CATEGORY_DESCRIPTIONS: dict[DriftCategory, str] = {
    DriftCategory.undocumented_node: "Code that has no node in the spec.",
    DriftCategory.missing_node: "A node whose code no longer exists.",
    DriftCategory.undocumented_edge: "A dependency in the code that the spec doesn't record.",
    DriftCategory.stale_edge: "An analyzed edge in the spec that the code no longer has.",
    DriftCategory.contract_mismatch: "A node's contract differs from the code's signature.",
    DriftCategory.invariant_violation: "An edge breaks a node's machine-checkable invariant.",
    DriftCategory.moved_node: "Code moved or was renamed; the spec has the old location.",
    DriftCategory.unenriched_node: "A node whose intent is still the placeholder.",
}


class Level(StrEnum):
    error = "error"
    warning = "warning"
    note = "note"


@dataclass(frozen=True)
class Finding:
    category: DriftCategory
    message: str
    key: str
    """Stable identity across commits; used to compare against a base revision."""
    node: str | None = None
    code: tuple[str, int] | None = None
    """(path relative to the spec's directory, line) of the code involved."""
    spec: tuple[Path, int | None] | None = None
    """(spec file, line) of the node, edge or invariant involved."""
    level: Level = Level.warning
    edge: tuple[str, str, str] | None = None
    """(from, type, to) of the edge involved, for edge and invariant findings."""


@dataclass
class DriftReport:
    spec_path: Path
    findings: list[Finding] = field(default_factory=list)
    files_analyzed: int = 0
    fail_on: frozenset[DriftCategory] = DEFAULT_FAIL_ON
    base: str | None = None
    suppressed: int = 0
    """Findings hidden because they already existed at ``base``."""

    def count(self, level: Level) -> int:
        return sum(1 for f in self.findings if f.level is level)

    @property
    def ok(self) -> bool:
        return self.count(Level.error) == 0


def detect(
    spec_path: Path,
    *,
    fail_on: frozenset[DriftCategory] | None = None,
    base: str | None = None,
    merged: Merged | None = None,
) -> DriftReport:
    """Compare the code with the spec. ``base`` (a git ref) hides findings that already existed.

    Raises ``SpecInvalidError`` if the spec has errors, and ``BaseRefError`` for a bad ``base``.
    """
    positions = _Positions(load(spec_path))  # from disk: the merge moves things around
    if merged is None:
        merged = analyze_and_merge(spec_path)
    document = merged.loaded.document
    assert document is not None
    config = document.config.drift if document.config else None
    if fail_on is None:
        fail_on = frozenset(config.fail_on) if config and config.fail_on else DEFAULT_FAIL_ON
    ignore_low = config.ignore_low_confidence if config else True

    findings = _Detector(merged, positions, ignore_low_confidence=ignore_low).run()
    report = DriftReport(
        spec_path=merged.loaded.path,
        findings=[_with_level(f, fail_on) for f in findings],
        files_analyzed=len(merged.analysis.files),
        fail_on=fail_on,
        base=base,
    )
    if base is not None:
        existing = keys_at(base, merged.loaded.path, fail_on=fail_on)
        kept = [f for f in report.findings if f.key not in existing]
        report.suppressed = len(report.findings) - len(kept)
        report.findings = kept
    return report


def _with_level(finding: Finding, fail_on: frozenset[DriftCategory]) -> Finding:
    if finding.category in fail_on:
        level = Level.error
    elif finding.category in NOTE_CATEGORIES:
        level = Level.note
    else:
        level = Level.warning
    return dataclasses.replace(finding, level=level)


class _Positions:
    """Lines of nodes, edges and invariants in the spec files as they are on disk."""

    def __init__(self, loaded: LoadedSpec) -> None:
        self.nodes: dict[str, tuple[Path, int | None]] = {}
        self.edges: dict[tuple[str, str, str], tuple[Path, int | None]] = {}
        self.invariants: dict[tuple[str, str], tuple[Path, int | None]] = {}
        for entry in loaded.nodes:
            data = entry.file.data
            self.nodes[entry.node.id] = (
                entry.file.path,
                yamlio.line_of(data, ("nodes", entry.index)),
            )
            for i, inv in enumerate(_invariants_of(entry.node)):
                path = ("nodes", entry.index, "governance", "invariants", i)
                self.invariants[(entry.node.id, inv.id)] = (
                    entry.file.path,
                    yamlio.line_of(data, path),
                )
        for edge_entry in loaded.edges:
            edge = edge_entry.edge
            self.edges[(edge.from_, edge.to, edge.type.value)] = (
                edge_entry.file.path,
                yamlio.line_of(edge_entry.file.data, ("edges", edge_entry.index)),
            )


class _Detector:
    def __init__(
        self, merged: Merged, positions: _Positions, *, ignore_low_confidence: bool
    ) -> None:
        self.loaded = merged.loaded
        self.analysis = merged.analysis
        self.report = merged.report
        self.positions = positions
        self.ignore_low = ignore_low_confidence
        self.symbols = {s.id: s for s in merged.analysis.symbols}
        self.out: list[Finding] = []

    def run(self) -> list[Finding]:
        self._nodes()
        self._edges()
        self._contracts()
        self._invariants()
        self._unenriched()
        return self.out

    def _code(self, node_id: str) -> tuple[str, int] | None:
        sym = self.report.symbol_of.get(node_id)
        return (sym.path, sym.line) if sym else None

    def _add(self, category: DriftCategory, message: str, key: str, **where: Any) -> None:
        self.out.append(Finding(category, message, f"{category.value}:{key}", **where))

    def _nodes(self) -> None:
        for node_id in self.report.added:
            sym = self.report.symbol_of.get(node_id)
            if sym is None:
                continue  # the repo root node the merge would create
            self._add(
                DriftCategory.undocumented_node,
                f"{_describe(sym)} has no node in the spec; run `intensify analyze` to add it",
                f"{sym.path}:{sym.symbol or ''}",
                code=(sym.path, sym.line),
            )
        for node_id, where in self.report.missing:
            self._add(
                DriftCategory.missing_node,
                f"node {node_id} points at {where}, which no longer exists",
                node_id,
                node=node_id,
                spec=self.positions.nodes.get(node_id),
            )
        for move in self.report.moved:
            self._add(
                DriftCategory.moved_node,
                f"node {move.id} moved from {move.old} to {move.new}",
                move.id,
                node=move.id,
                code=self._code(move.id),
                spec=self.positions.nodes.get(move.id),
            )

    def _edges(self) -> None:
        new_nodes = set(self.report.added)
        for (source, target, edge_type), dep in self.report.new_edges:
            if source in new_nodes or target in new_nodes:
                continue  # reported with the undocumented node
            if self.ignore_low and dep.confidence.value == "low":
                continue
            sym = self.symbols.get(dep.source)
            self._add(
                DriftCategory.undocumented_edge,
                f"{source} {edge_type} {target}, which the spec doesn't record",
                f"{source}:{edge_type}:{target}",
                node=source,
                code=(sym.path, sym.line) if sym else None,
            )
        low = {
            (e.edge.from_, e.edge.to, e.edge.type.value)
            for e in self.loaded.edges
            if e.edge.confidence.value == "low"
        }
        for key in self.report.stale_edges:
            if self.ignore_low and key in low:
                continue
            source, target, edge_type = key
            self._add(
                DriftCategory.stale_edge,
                f"the spec records {source} {edge_type} {target}, but the code no longer has it",
                f"{source}:{edge_type}:{target}",
                node=source,
                spec=self.positions.edges.get(key),
            )

    def _contracts(self) -> None:
        for entry in self.loaded.nodes:
            node = entry.node
            sym = self.report.symbol_of.get(node.id)
            if sym is None:
                continue
            if node.provenance is Provenance.analyzed:
                spec_contract = (
                    node.contract.model_dump(by_alias=True, exclude_none=True)
                    if node.contract
                    else None
                )
                code_contract = _plain_contract(sym.contract)
                if _normalize(spec_contract) != _normalize(code_contract):
                    self._add(
                        DriftCategory.contract_mismatch,
                        f"the contract of {node.id} is out of date with the code; "
                        "run `intensify analyze` to refresh it",
                        node.id,
                        node=node.id,
                        code=(sym.path, sym.line),
                        spec=self.positions.nodes.get(node.id),
                    )
            elif node.contract is not None:
                problems = _contract_problems(node.contract, sym.contract or {})
                if problems:
                    self._add(
                        DriftCategory.contract_mismatch,
                        f"{node.id}: " + "; ".join(problems),
                        node.id,
                        node=node.id,
                        code=(sym.path, sym.line),
                        spec=self.positions.nodes.get(node.id),
                    )

    def _invariants(self) -> None:
        graph = _Graph(self.loaded)
        for entry in self.loaded.nodes:
            for inv in _invariants_of(entry.node):
                if inv.rule is None:
                    continue
                node_id = entry.node.id
                inside = graph.subtree(node_id)
                where = self.positions.invariants.get((node_id, inv.id))
                if (forbid := inv.rule.forbid_edge) is not None:
                    for edge in graph.edges:
                        if graph.local(edge[0]) in inside and _matches(
                            edge, forbid.type, forbid.to
                        ):
                            self._violation(node_id, inv, edge, where)
                elif (require := inv.rule.require_edge) is not None:
                    if not any(
                        graph.local(edge[0]) in inside and _matches(edge, require.type, require.to)
                        for edge in graph.edges
                    ):
                        kind = f"{require.type.value} " if require.type else ""
                        self._add(
                            DriftCategory.invariant_violation,
                            f"{node_id} invariant {inv.id!r}: requires a {kind}edge to "
                            f"{require.to!r}, but there is none ({inv.text})",
                            f"{node_id}:{inv.id}",
                            node=node_id,
                            spec=where,
                        )
                elif (allowed := inv.rule.allowed_dependents) is not None:
                    for edge in graph.edges:
                        source, edge_type, target = edge
                        if (
                            graph.local(target) in inside
                            and graph.local(source) not in inside
                            and (allowed.type is None or allowed.type.value == edge_type)
                            and not any(fnmatch.fnmatchcase(source, g) for g in allowed.from_)
                        ):
                            self._violation(node_id, inv, edge, where)

    def _violation(
        self,
        node_id: str,
        inv: Invariant,
        edge: tuple[str, str, str],
        where: tuple[Path, int | None] | None,
    ) -> None:
        source, edge_type, target = edge
        self._add(
            DriftCategory.invariant_violation,
            f"{node_id} invariant {inv.id!r}: {source} {edge_type} {target} is not allowed "
            f"({inv.text})",
            f"{node_id}:{inv.id}:{source}:{edge_type}:{target}",
            node=node_id,
            edge=edge,
            code=self._code(source),
            spec=where,
        )

    def _unenriched(self) -> None:
        for entry in self.loaded.nodes:
            if entry.node.intent.summary == PLACEHOLDER_SUMMARY:
                self._add(
                    DriftCategory.unenriched_node,
                    f"node {entry.node.id} has no intent yet",
                    entry.node.id,
                    node=entry.node.id,
                    spec=self.positions.nodes.get(entry.node.id),
                )


class _Graph:
    """Nodes and edges of the spec after the merge: what the code and spec say together."""

    def __init__(self, loaded: LoadedSpec) -> None:
        self.own = loaded.document.project.id if loaded.document else ""
        self.children: dict[str, list[str]] = defaultdict(list)
        self.edges: list[tuple[str, str, str]] = []
        """(from, type, to), excluding deprecated edges."""
        for file in loaded.files:
            for item in file.data.get("nodes") or []:
                if (parent := item.get("parent_id")) is not None:
                    self.children[str(parent)].append(str(item["id"]))
            for item in file.data.get("edges") or []:
                if item.get("lifecycle_state") != LifecycleState.deprecated.value:
                    self.edges.append((str(item["from"]), str(item["type"]), str(item["to"])))

    def subtree(self, root: str) -> set[str]:
        out: set[str] = set()
        stack = [root]
        while stack:
            current = stack.pop()
            if current not in out:
                out.add(current)
                stack.extend(self.children.get(current, []))
        return out

    def local(self, ref: str) -> str:
        """``<own-project>:x`` -> ``x``; other refs unchanged."""
        project, sep, rest = ref.rpartition(":")
        return rest if sep and project == self.own else ref


def _matches(edge: tuple[str, str, str], edge_type: Any, target_glob: str) -> bool:
    _, actual_type, target = edge
    return (edge_type is None or edge_type.value == actual_type) and fnmatch.fnmatchcase(
        target, target_glob
    )


def _invariants_of(node: Node) -> list[Invariant]:
    return (node.governance.invariants or []) if node.governance else []


def _describe(sym: Symbol) -> str:
    kind = "class" if sym.kind.value == "class" else sym.kind.value
    return f"{kind} {sym.symbol}" if sym.symbol else f"module {sym.path}"


def _plain_contract(contract: dict[str, Any] | None) -> dict[str, Any] | None:
    if not contract:
        return None
    return Contract.model_validate(contract).model_dump(by_alias=True, exclude_none=True)


def _normalize(contract: dict[str, Any] | None) -> Any:
    """Contracts compare equal when they only differ in defaulted fields or whitespace."""
    if not contract:
        return None
    out: dict[str, Any] = {}
    for key in ("inputs", "outputs", "errors"):
        items = contract.get(key) or []
        cleaned = []
        for item in items:
            entry = {k: _squash(v) for k, v in item.items() if v is not None}
            if key == "inputs":
                entry.setdefault("required", True)
            cleaned.append(entry)
        if cleaned:
            out[key] = cleaned
    return out or None


def _squash(value: Any) -> Any:
    return " ".join(value.split()) if isinstance(value, str) else value


def _contract_problems(spec: Contract, code: dict[str, Any]) -> list[str]:
    """Differences between a hand-written contract and the code, on the parts the spec declares.

    Error modes are not compared: the analyzer only sees exceptions raised directly in the body.
    """
    problems: list[str] = []
    code_inputs = code.get("inputs") or []
    if spec.inputs is not None:
        spec_names = [p.name for p in spec.inputs]
        code_names = [p["name"] for p in code_inputs]
        if spec_names != code_names:
            problems.append(
                f"inputs are ({', '.join(spec_names)}) in the spec but ({', '.join(code_names)}) "
                "in the code"
            )
        else:
            for declared, actual in zip(spec.inputs, code_inputs, strict=True):
                code_type = actual.get("type")
                if declared.type and code_type and _squash(declared.type) != _squash(code_type):
                    problems.append(
                        f"input {declared.name!r} is {declared.type!r} in the spec but "
                        f"{code_type!r} in the code"
                    )
                if declared.required != actual.get("required", True):
                    state = "required" if declared.required else "optional"
                    problems.append(
                        f"input {declared.name!r} is {state} in the spec but not in the code"
                    )
    declared_out = next((o.type for o in spec.outputs or [] if o.type), None)
    code_out = next((o.get("type") for o in code.get("outputs") or [] if o.get("type")), None)
    if declared_out and code_out and _squash(declared_out) != _squash(code_out):
        problems.append(f"returns {declared_out!r} in the spec but {code_out!r} in the code")
    return problems
