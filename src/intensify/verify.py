"""Check a node's code against its spec, and mark the node ``active`` when it passes.

Checks, in order: the code exists; the contract matches; no invariant is broken by an edge into
or out of the node; at least one test verifies it; and the configured test command passes.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from intensify.differ import detect
from intensify.graph import Graph
from intensify.pipeline import analyze_and_merge
from intensify.spec.edit import find, put, save
from intensify.spec.models import DriftCategory, LifecycleState

TEST_OUTPUT_LINES = 40
"""Lines of test output kept when the test command fails."""


class NodeNotFoundError(Exception):
    pass


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    skipped: bool = False


@dataclass
class VerifyResult:
    node_id: str
    checks: list[Check] = field(default_factory=list)
    promoted: bool = False
    """Whether the node was (or, in a dry run, would be) marked active."""
    previous_state: LifecycleState | None = None

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def verify(
    spec_path: Path,
    node_id: str,
    *,
    run_tests: bool = True,
    require_tests: bool = True,
    dry_run: bool = False,
) -> VerifyResult:
    """Raises ``SpecInvalidError`` for an invalid spec and ``NodeNotFoundError`` for a bad ID."""
    merged = analyze_and_merge(spec_path)
    loaded = merged.loaded
    node = next((e.node for e in loaded.nodes if e.node.id == node_id), None)
    if node is None:
        raise NodeNotFoundError(node_id)
    result = VerifyResult(node_id, previous_state=node.lifecycle_state)

    symbol = merged.report.symbol_of.get(node_id)
    if symbol is None:
        where = ""
        if node.technical and node.technical.location:
            where = f" at {node.technical.location.path}"
        result.checks.append(
            Check("code exists", ok=False, detail=f"no code found for this node{where}")
        )
        return result
    location = f"{symbol.path}:{symbol.symbol}" if symbol.symbol else symbol.path
    result.checks.append(Check("code exists", ok=True, detail=location))

    # The merged spec (the spec plus what the code says now) decides tests and invariants.
    graph = Graph.from_data(loaded)
    inside = {node_id, *graph.descendants(node_id)}
    report = detect(spec_path, merged=merged, fail_on=frozenset())

    mismatches = [
        f.message
        for f in report.findings
        if f.category is DriftCategory.contract_mismatch and f.node == node_id
    ]
    result.checks.append(
        Check("contract matches", ok=not mismatches, detail="; ".join(mismatches))
        if node.contract or mismatches
        else Check("contract matches", ok=True, detail="no contract declared", skipped=True)
    )

    violations = [
        f.message
        for f in report.findings
        if f.category is DriftCategory.invariant_violation
        and (
            (f.edge is not None and (f.edge[0] in inside or f.edge[2] in inside))
            or (f.edge is None and f.node in inside)
        )
    ]
    result.checks.append(Check("invariants hold", ok=not violations, detail="; ".join(violations)))

    tests: dict[str, str] = {}
    for test_id in graph.tests_of(node_id):
        test = graph.get(test_id)
        if test is not None and test.technical and test.technical.location:
            tests[test_id] = test.technical.location.path
    if tests:
        result.checks.append(Check("has tests", ok=True, detail=", ".join(sorted(tests))))
    elif require_tests:
        result.checks.append(
            Check("has tests", ok=False, detail="no test verifies this node; add one")
        )
    else:
        result.checks.append(Check("has tests", ok=True, detail="not required", skipped=True))

    document = loaded.document
    command = (
        document.config.verify.test_command
        if document and document.config and document.config.verify
        else None
    )
    if not run_tests:
        result.checks.append(Check("tests pass", ok=True, detail="skipped", skipped=True))
    elif command is None:
        result.checks.append(
            Check("tests pass", ok=True, detail="no config.verify.test_command", skipped=True)
        )
    elif not tests:
        result.checks.append(Check("tests pass", ok=True, detail="no tests to run", skipped=True))
    else:
        result.checks.append(_run_tests(command, sorted(tests.values()), loaded.path.parent))

    if result.ok:
        result.promoted = node.lifecycle_state is not LifecycleState.active
        if not dry_run:
            _record(spec_path, node_id, symbol.path)
    return result


def _run_tests(command: str, files: list[str], cwd: Path) -> Check:
    quoted = " ".join(shlex.quote(f) for f in files)
    expanded = command.replace("{tests}", quoted) if "{tests}" in command else command
    try:
        proc = subprocess.run(
            shlex.split(expanded), cwd=cwd, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        return Check("tests pass", ok=False, detail=f"could not run `{expanded}`: {exc}")
    if proc.returncode == 0:
        return Check("tests pass", ok=True, detail=f"`{expanded}`")
    output = (proc.stdout + proc.stderr).strip().splitlines()[-TEST_OUTPUT_LINES:]
    return Check(
        "tests pass",
        ok=False,
        detail=f"`{expanded}` exited {proc.returncode}\n" + "\n".join(output),
    )


def _record(spec_path: Path, node_id: str, path: str) -> None:
    """Write the analysis for the node's file, and mark the node active."""
    merged = analyze_and_merge(spec_path, paths=(path,))
    _, node = find(merged.loaded, node_id)
    put(node, "lifecycle_state", LifecycleState.active.value)
    save(merged.loaded)
