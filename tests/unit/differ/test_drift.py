from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from intensify.cli.init import render
from intensify.differ import DriftReport, Level, detect
from intensify.differ.base import BaseRefError
from intensify.differ.report import MARKER, to_codequality, to_json, to_markdown, to_sarif
from intensify.pipeline import SpecInvalidError
from intensify.pipeline import analyze_and_write as analyze
from intensify.spec import yamlio
from intensify.spec.models import DriftCategory as C
from tests.conftest import CopyRepo


@pytest.fixture
def repo(copy_repo: CopyRepo) -> Path:
    root = copy_repo("py_shop")
    (root / "intensify.yaml").write_text(render("py-shop", "Shop", "A tiny shop."))
    analyze(root)
    return root


def edit_code(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new, 1))


def edit_spec(root: Path, change: Callable[[dict[str, Any]], None], node_id: str) -> None:
    """Edit one node of the spec through the round-trip loader."""
    spec = root / "intensify.yaml"
    data = yamlio.read(spec)
    change(next(n for n in data["nodes"] if n["id"] == node_id))
    yamlio.write(data, spec)


def found(report: DriftReport, *, notes: bool = False) -> set[tuple[str, str | None]]:
    return {
        (f.category.value, f.node) for f in report.findings if notes or f.level is not Level.note
    }


def test_fresh_spec_has_only_unenriched_notes(repo: Path) -> None:
    report = detect(repo)
    assert report.ok
    assert {f.category for f in report.findings} == {C.unenriched_node}
    assert all(f.level is Level.note for f in report.findings)


def test_volatile_changes_are_not_drift(repo: Path) -> None:
    # A body-only edit changes complexity, loc, fingerprint and nothing else.
    edit_code(
        repo / "src/shop/pricing.py", "return cents // 2", "half = cents // 2\n        return half"
    )
    assert found(detect(repo)) == set()


def test_undocumented_node_and_its_edges_are_one_finding(repo: Path) -> None:
    edit_code(
        repo / "src/shop/cart.py",
        "class CartError",
        "def empty_cart(owner: str) -> Cart:\n    return Cart(owner)\n\n\nclass CartError",
    )
    [finding] = [f for f in detect(repo).findings if f.level is not Level.note]
    assert finding.category is C.undocumented_node
    assert finding.code == ("src/shop/cart.py", 10)
    assert finding.level is Level.warning  # not in the default fail_on


def test_new_and_removed_dependencies(repo: Path) -> None:
    edit_code(
        repo / "src/shop/pricing.py",
        "return round(cents * (1 + TAX_RATE))",
        "return round(apply_discount(cents, 'X') * (1 + TAX_RATE))",
    )
    edit_code(
        repo / "src/shop/cart.py",
        "return discount(subtotal, code) if code else subtotal",
        "return subtotal",
    )
    report = detect(repo)
    assert found(report) == {
        ("undocumented_edge", "shop.pricing.with_tax"),
        ("stale_edge", "shop.cart.Cart.total"),
    }
    by_category = {f.category: f for f in report.findings}
    assert by_category[C.undocumented_edge].level is Level.error
    assert by_category[C.undocumented_edge].code == ("src/shop/pricing.py", 18)
    assert by_category[C.stale_edge].level is Level.warning
    stale_spec = by_category[C.stale_edge].spec
    assert stale_spec is not None
    assert stale_spec[0].name == "intensify.yaml"
    assert not report.ok


def test_missing_and_moved_nodes(repo: Path) -> None:
    edit_code(
        repo / "src/shop/pricing.py",
        "def with_tax(cents: int) -> int:\n    return round(cents * (1 + TAX_RATE))\n",
        "",
    )
    edit_code(repo / "src/shop/pricing.py", "def subtotal(", "def sum_items(")
    edit_code(repo / "src/shop/cart.py", "pricing.subtotal(", "pricing.sum_items(")
    report = detect(repo)
    assert ("missing_node", "shop.pricing.with_tax") in found(report)
    moved = [f for f in report.findings if f.category is C.moved_node]
    assert [(f.node, f.level) for f in moved] == [("shop.pricing.subtotal", Level.note)]


def test_planned_node_with_future_location_is_not_missing(repo: Path) -> None:
    spec = repo / "intensify.yaml"
    data = yamlio.read(spec)
    data["nodes"].append(
        yamlio.loads(
            "id: shop.loyalty\nkind: module\nparent_id: shop\nintent: {summary: Points.}\n"
            "technical: {location: {path: src/shop/loyalty.py}}\n"
        )
    )
    yamlio.write(data, spec)
    assert found(detect(repo)) == set()


def test_analyzed_contract_out_of_date(repo: Path) -> None:
    edit_code(
        repo / "src/shop/pricing.py",
        "def with_tax(cents: int)",
        "def with_tax(cents: int, rate: float = 0.2)",
    )
    [finding] = [f for f in detect(repo).findings if f.level is not Level.note]
    assert finding.category is C.contract_mismatch
    assert "run `intensify analyze`" in finding.message
    assert finding.level is Level.error


@pytest.mark.parametrize(
    ("contract", "problem"),
    [
        pytest.param(
            {"inputs": [{"name": "cents"}]},
            "inputs are (cents) in the spec but (cents, code)",
            id="names",
        ),
        pytest.param(
            {"inputs": [{"name": "cents", "type": "float"}, {"name": "code"}]},
            "input 'cents' is 'float' in the spec but 'int' in the code",
            id="type",
        ),
        pytest.param(
            {"inputs": [{"name": "cents"}, {"name": "code", "required": False}]},
            "input 'code' is optional in the spec but not in the code",
            id="required",
        ),
        pytest.param(
            {"outputs": [{"type": "str"}]}, "returns 'str' in the spec but 'int'", id="output"
        ),
        pytest.param(
            {"inputs": [{"name": "cents", "type": "int"}, {"name": "code"}]}, None, id="matches"
        ),
        pytest.param({"errors": [{"type": "Anything"}]}, None, id="errors-not-compared"),
    ],
)
def test_hand_written_contract(repo: Path, contract: dict[str, Any], problem: str | None) -> None:
    def change(node: dict[str, Any]) -> None:
        node["provenance"] = "manual"
        node["contract"] = contract

    edit_spec(repo, change, "shop.pricing.apply_discount")
    findings = [f for f in detect(repo).findings if f.category is C.contract_mismatch]
    if problem is None:
        assert findings == []
    else:
        [finding] = findings
        assert problem in finding.message


def add_invariant(root: Path, node_id: str, rule: dict[str, Any]) -> None:
    def change(node: dict[str, Any]) -> None:
        node["governance"] = {"invariants": [{"id": "rule", "text": "The rule.", "rule": rule}]}

    edit_spec(root, change, node_id)


def test_forbid_edge_checks_the_node_and_its_descendants(repo: Path) -> None:
    add_invariant(repo, "shop.cart", {"forbid_edge": {"type": "CALLS", "to": "shop.pricing.*"}})
    violations = [f for f in detect(repo).findings if f.category is C.invariant_violation]
    assert sorted(f.message.split(": ", 1)[1] for f in violations) == [
        "shop.cart.Cart.total CALLS shop.pricing.apply_discount is not allowed (The rule.)",
        "shop.cart.Cart.total CALLS shop.pricing.subtotal is not allowed (The rule.)",
    ]
    assert violations[0].code == ("src/shop/cart.py", 28)
    assert violations[0].level is Level.error


def test_forbid_edge_sees_new_code_edges(repo: Path) -> None:
    add_invariant(repo, "shop.pricing", {"forbid_edge": {"to": "shop.cart*"}})
    assert found(detect(repo)) == set()
    edit_code(
        repo / "src/shop/pricing.py",
        '"""Price calculations."""',
        '"""Price calculations."""\n\nfrom shop.cart import CartError',
    )
    edit_code(repo / "src/shop/pricing.py", "return cents // 2", "raise CartError('no')")
    assert ("invariant_violation", "shop.pricing") in found(detect(repo))


def test_require_edge(repo: Path) -> None:
    add_invariant(repo, "shop.cart", {"require_edge": {"type": "CALLS", "to": "shop.pricing.*"}})
    assert found(detect(repo)) == set()
    edit_code(
        repo / "src/shop/cart.py",
        "subtotal = pricing.subtotal(self.items)\n"
        "        return discount(subtotal, code) if code else subtotal",
        "return 0",
    )
    assert ("invariant_violation", "shop.cart") in found(detect(repo))


def test_allowed_dependents(repo: Path) -> None:
    add_invariant(repo, "shop.pricing", {"allowed_dependents": {"from": ["tests.*"]}})
    callers = {
        f.message.split(": ", 1)[1].split(" ", 1)[0]
        for f in detect(repo).findings
        if f.category is C.invariant_violation
    }
    assert callers == {"shop.cart.Cart.total"}  # the test module is allowed


def test_fail_on_from_config_and_override(repo: Path) -> None:
    edit_code(
        repo / "src/shop/cart.py",
        "class CartError",
        "def empty_cart() -> None:\n    pass\n\n\nclass CartError",
    )
    assert detect(repo).ok
    assert not detect(repo, fail_on=frozenset({C.undocumented_node})).ok
    spec = repo / "intensify.yaml"
    data = yamlio.read(spec)
    data["config"]["drift"]["fail_on"] = ["undocumented_node"]
    yamlio.write(data, spec)
    assert not detect(repo).ok


def test_invalid_spec_raises(repo: Path) -> None:
    (repo / "intensify.yaml").write_text('intensify: "1.0"\nproject: {id: Bad}\n')
    with pytest.raises(SpecInvalidError):
        detect(repo)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class TestBase:
    @pytest.fixture
    def committed(self, repo: Path) -> Path:
        git(repo, "init", "-q", "-b", "main")
        git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
        return repo

    def test_only_new_drift_is_reported(self, committed: Path) -> None:
        # Drift already on the base branch...
        edit_code(
            committed / "src/shop/cart.py",
            "return discount(subtotal, code) if code else subtotal",
            "return subtotal",
        )
        git(committed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "old drift")
        git(committed, "branch", "base")
        # ...and drift introduced on top of it.
        edit_code(
            committed / "src/shop/pricing.py",
            "return round(cents * (1 + TAX_RATE))",
            "return round(apply_discount(cents, 'X'))",
        )

        everything = detect(committed)
        assert {f.category for f in everything.findings if f.level is not Level.note} == {
            C.stale_edge,
            C.undocumented_edge,
        }

        report = detect(committed, base="base")
        assert {f.category for f in report.findings} == {C.undocumented_edge}
        assert report.suppressed == len(everything.findings) - 1

    def test_spec_added_after_base(self, committed: Path) -> None:
        git(committed, "rm", "-q", "--cached", "intensify.yaml")
        git(committed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "no spec")
        git(committed, "branch", "base")
        report = detect(committed, base="base")
        assert report.suppressed == 0

    def test_unknown_ref(self, committed: Path) -> None:
        with pytest.raises(BaseRefError, match="nope"):
            detect(committed, base="nope")

    def test_not_a_git_repo(self, repo: Path) -> None:
        with pytest.raises(BaseRefError, match="not inside a git repository"):
            detect(repo, base="main")


class TestReports:
    @pytest.fixture
    def report(self, repo: Path) -> DriftReport:
        edit_code(
            repo / "src/shop/pricing.py",
            "return round(cents * (1 + TAX_RATE))",
            "return round(apply_discount(cents, 'X'))",
        )
        edit_code(
            repo / "src/shop/cart.py",
            "return discount(subtotal, code) if code else subtotal",
            "return subtotal",
        )
        return detect(repo)

    def test_sarif(self, report: DriftReport) -> None:
        sarif = json.loads(to_sarif(report))
        assert sarif["version"] == "2.1.0"
        [run] = sarif["runs"]
        rules = run["tool"]["driver"]["rules"]
        assert [r["id"] for r in rules] == [c.value for c in C]
        by_rule = {r["ruleId"]: r for r in run["results"]}
        edge = by_rule["undocumented_edge"]
        assert edge["level"] == "error"
        assert rules[edge["ruleIndex"]]["id"] == "undocumented_edge"
        assert edge["locations"][0]["physicalLocation"] == {
            "artifactLocation": {"uri": "src/shop/pricing.py", "uriBaseId": "%SRCROOT%"},
            "region": {"startLine": 18},
        }
        stale = by_rule["stale_edge"]["locations"][0]["physicalLocation"]
        assert stale["artifactLocation"]["uri"] == "intensify.yaml"
        assert len(edge["partialFingerprints"]["intensifyFindingKey/v1"]) == 64

    def test_markdown(self, report: DriftReport) -> None:
        text = to_markdown(report)
        assert text.startswith(MARKER + "\n### Intensify drift: ❌ 1 error, 1 warning,")
        assert "<details open><summary>❌ <b>undocumented_edge</b> (1)" in text
        assert "- `src/shop/pricing.py:18` shop.pricing.with_tax CALLS" in text

    def test_markdown_when_clean(self, repo: Path) -> None:
        text = to_markdown(detect(repo, fail_on=frozenset()))
        assert "### Intensify drift: ⚠️" in text or "### Intensify drift: ✅" in text

    def test_codequality(self, report: DriftReport) -> None:
        issues = json.loads(to_codequality(report))
        edge = next(i for i in issues if i["check_name"] == "intensify/undocumented_edge")
        assert edge["severity"] == "major"
        assert edge["location"] == {"path": "src/shop/pricing.py", "lines": {"begin": 18}}
        assert len({i["fingerprint"] for i in issues}) == len(issues)

    def test_json(self, report: DriftReport) -> None:
        payload = json.loads(to_json(report))
        assert payload["ok"] is False
        assert payload["summary"]["error"] == 1
        edge = next(f for f in payload["findings"] if f["category"] == "undocumented_edge")
        assert edge["code"] == {"path": "src/shop/pricing.py", "line": 18}
