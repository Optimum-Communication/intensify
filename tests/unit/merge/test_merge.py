"""End-to-end merge behaviour: what `analyze` may and may not change in an existing spec."""

from __future__ import annotations

from pathlib import Path

import pytest

from intensify.cli.analyze import run
from intensify.cli.init import render
from intensify.spec import PLACEHOLDER_SUMMARY, load_and_validate
from intensify.spec.models import Node
from tests.conftest import CopyRepo


@pytest.fixture
def repo(copy_repo: CopyRepo) -> Path:
    root = copy_repo("py_shop")
    (root / "intensify.yaml").write_text(render("py-shop", "Shop", "A tiny shop."))
    run(root)
    return root


def nodes(root: Path) -> dict[str, Node]:
    loaded = load_and_validate(root)
    assert loaded.ok, loaded.diagnostics
    return {e.node.id: e.node for e in loaded.nodes}


def edge_keys(root: Path) -> set[tuple[str, str, str]]:
    return {(e.edge.from_, e.edge.to, e.edge.type.value) for e in load_and_validate(root).edges}


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new, 1))


def test_second_run_changes_nothing(repo: Path) -> None:
    before = (repo / "intensify.yaml").read_text()
    outcome = run(repo)
    assert outcome.diffs == {}
    assert not outcome.report.changed
    assert (repo / "intensify.yaml").read_text() == before


def test_new_nodes_are_active_analyzed_placeholders(repo: Path) -> None:
    node = nodes(repo)["shop.pricing.apply_discount"]
    assert node.lifecycle_state == "active"
    assert node.provenance == "analyzed"
    assert node.intent.summary == PLACEHOLDER_SUMMARY
    assert node.parent_id == "shop.pricing"
    assert node.technical is not None
    assert node.technical.location is not None
    assert node.technical.location.symbol == "apply_discount"


def test_edges_into_private_code_are_dropped(repo: Path) -> None:
    keys = edge_keys(repo)
    assert ("shop.cart.Cart.total", "shop.pricing.subtotal", "CALLS") in keys
    assert not any(target in {"shop", "shop._internal"} for _, target, _ in keys)


def test_human_fields_and_comments_survive(repo: Path) -> None:
    spec = repo / "intensify.yaml"
    edit(
        spec,
        '    intent:\n      summary: "TODO: describe intent"\n    contract:\n      inputs:\n'
        "        - {name: sku, type: str}",
        "    intent:\n      # keep me\n      summary: Add a line item.\n    governance:\n"
        '      owners: ["@shop"]\n    contract:\n      inputs:\n        - {name: sku, type: str}',
    )
    edit(
        spec, "edges:\n", "edges:\n  - {from: shop.cart.Cart, to: shop.pricing, type: READS_FROM}\n"
    )
    edit(repo / "src/shop/cart.py", "qty: int = 1", "qty: int = 2")  # code change too
    before = spec.read_text()

    run(repo)
    after = spec.read_text()
    assert "      # keep me\n      summary: Add a line item.\n" in after
    assert '      owners: ["@shop"]' in after
    assert "  - {from: shop.cart.Cart, to: shop.pricing, type: READS_FROM}\n" in after
    assert before != after  # the signature changed...
    assert "qty: int = 2" in after


def test_rename_and_move_keep_the_node_id(repo: Path) -> None:
    edit(repo / "src/shop/pricing.py", "def subtotal(", "def sum_items(")
    edit(repo / "src/shop/cart.py", "pricing.subtotal(", "pricing.sum_items(")
    edit(
        repo / "src/shop/pricing.py",
        "\n\ndef with_tax(cents: int) -> int:\n    return round(cents * (1 + TAX_RATE))\n",
        "\n",
    )
    (repo / "src/shop/tax.py").write_text(
        "TAX_RATE = 0.2\n\n\n"
        "def with_tax(cents: int) -> int:\n    return round(cents * (1 + TAX_RATE))\n"
    )

    report = run(repo).report
    moved = {m.id: (m.old, m.new) for m in report.moved}
    assert moved["shop.pricing.subtotal"] == (
        "src/shop/pricing.py:subtotal",
        "src/shop/pricing.py:sum_items",
    )
    assert moved["shop.pricing.with_tax"][1] == "src/shop/tax.py:with_tax"
    after = nodes(repo)
    assert after["shop.pricing.with_tax"].parent_id == "shop.tax"  # analyzed: re-parented
    assert ("shop.cart.Cart.total", "shop.pricing.subtotal", "CALLS") in edge_keys(repo)


def test_edited_and_moved_symbol_matches_by_name_and_signature(repo: Path) -> None:
    edit(
        repo / "src/shop/pricing.py",
        "def with_tax(cents: int) -> int:\n    return round(cents * (1 + TAX_RATE))\n",
        "",
    )
    (repo / "src/shop/tax.py").write_text(
        "def with_tax(cents: int) -> int:\n    rate = 0.25\n    return round(cents * (1 + rate))\n"
    )
    report = run(repo).report
    assert [m.id for m in report.moved] == ["shop.pricing.with_tax"]
    assert "shop.pricing.with_tax" not in {node_id for node_id, _ in report.missing}


def test_planned_node_is_promoted_when_implemented(repo: Path) -> None:
    spec = repo / "intensify.yaml"
    edit(
        spec,
        "  - id: tests.test_cart\n",
        "  - id: shop.pricing.bulk_discount\n    kind: function\n    parent_id: shop.pricing\n"
        "    intent:\n      summary: Ten percent off big orders.\n  - id: tests.test_cart\n",
    )
    (repo / "src/shop/pricing.py").write_text(
        (repo / "src/shop/pricing.py").read_text()
        + "\n\ndef bulk_discount(cents: int, count: int) -> int:\n    return cents\n"
    )
    report = run(repo).report
    assert report.promoted == ["shop.pricing.bulk_discount"]
    node = nodes(repo)["shop.pricing.bulk_discount"]
    assert node.lifecycle_state == "in_progress"
    assert node.provenance == "manual"
    assert node.intent.summary == "Ten percent off big orders."
    assert node.contract is None  # human-owned on manual nodes
    text = spec.read_text()
    block = text[text.index("  - id: shop.pricing.bulk_discount") :]
    assert block.index("lifecycle_state: in_progress") < block.index("intent:")


def test_signature_change_updates_analyzed_contract(repo: Path) -> None:
    edit(
        repo / "src/shop/pricing.py",
        "def with_tax(cents: int)",
        "def with_tax(cents: int, rate: float = 0.2)",
    )
    run(repo)
    contract = nodes(repo)["shop.pricing.with_tax"].contract
    assert contract is not None
    assert [p.name for p in contract.inputs or []] == ["cents", "rate"]


def test_deleted_code_is_reported_then_deprecated_with_prune(repo: Path) -> None:
    edit(
        repo / "src/shop/pricing.py",
        "def with_tax(cents: int) -> int:\n    return round(cents * (1 + TAX_RATE))\n",
        "",
    )
    report = run(repo).report
    assert [node_id for node_id, _ in report.missing] == ["shop.pricing.with_tax"]
    assert nodes(repo)["shop.pricing.with_tax"].lifecycle_state == "active"  # never deleted

    report = run(repo, prune=True).report
    assert report.deprecated == ["shop.pricing.with_tax"]
    assert nodes(repo)["shop.pricing.with_tax"].lifecycle_state == "deprecated"
    assert run(repo, prune=True).report.missing == []  # already deprecated


def test_stale_analyzed_edges_are_removed_but_manual_edges_stay(repo: Path) -> None:
    spec = repo / "intensify.yaml"
    edit(
        spec,
        "edges:\n",
        "edges:\n  - {from: shop.cart.Cart.total, to: shop.pricing.with_tax, type: CALLS}\n",
    )
    edit(
        repo / "src/shop/cart.py",
        "return discount(subtotal, code) if code else subtotal",
        "return subtotal",
    )
    report = run(repo).report
    assert report.edges_removed == 1
    keys = edge_keys(repo)
    assert ("shop.cart.Cart.total", "shop.pricing.apply_discount", "CALLS") not in keys
    assert ("shop.cart.Cart.total", "shop.pricing.with_tax", "CALLS") in keys


def test_paths_limits_changes(repo: Path) -> None:
    edit(
        repo / "src/shop/pricing.py",
        "def with_tax(cents: int)",
        "def with_tax(cents: int, x: int = 0)",
    )
    edit(repo / "src/shop/cart.py", "def total(self, code", "def total(self, extra: int, code")
    report = run(repo, paths=("src/shop/cart.py",)).report
    assert report.updated == ["shop.cart", "shop.cart.Cart", "shop.cart.Cart.total"]
    contract = nodes(repo)["shop.pricing.with_tax"].contract
    assert contract is not None
    assert len(contract.inputs or []) == 1


def test_dry_run_writes_nothing(repo: Path) -> None:
    edit(
        repo / "src/shop/pricing.py",
        "def with_tax(cents: int)",
        "def with_tax(cents: int, x: int = 0)",
    )
    before = (repo / "intensify.yaml").read_text()
    outcome = run(repo, dry_run=True)
    assert (repo / "intensify.yaml").read_text() == before
    [diff] = outcome.diffs.values()
    assert "+      signature: 'def with_tax(cents: int, x: int = 0) -> int'" in diff


def test_missing_root_node_is_created(copy_repo: CopyRepo) -> None:
    root = copy_repo("py_shop")
    (root / "intensify.yaml").write_text('intensify: "1.0"\nproject: {id: shop-repo}\n')
    report = run(root).report
    assert report.added[0] == "shop-repo"
    assert nodes(root)["shop"].parent_id == "shop-repo"


def test_new_nodes_go_to_the_parents_fragment_file(copy_repo: CopyRepo) -> None:
    root = copy_repo("py_shop")
    (root / "intensify.yaml").write_text(
        'intensify: "1.0"\nproject: {id: shop-repo}\nincludes: [".intensify/*.yaml"]\nnodes:\n'
        "  - {id: shop-repo, kind: repo, lifecycle_state: active, intent: {summary: Root}}\n"
    )
    (root / ".intensify").mkdir()
    (root / ".intensify/pricing.yaml").write_text(
        'intensify: "1.0"\nnodes:\n  - id: shop.pricing\n    kind: module\n'
        "    parent_id: shop-repo\n"
        "    lifecycle_state: active\n    intent: {summary: Prices.}\n"
        "    technical:\n      location: {path: src/shop/pricing.py}\n"
    )
    run(root)
    fragment = (root / ".intensify/pricing.yaml").read_text()
    assert "id: shop.pricing.apply_discount" in fragment
    assert "id: shop.pricing.apply_discount" not in (root / "intensify.yaml").read_text()
    assert load_and_validate(root).ok


def test_planned_module_is_not_matched_to_an_unrelated_module(repo: Path) -> None:
    spec = repo / "intensify.yaml"
    edit(
        spec,
        "  - id: tests.test_cart\n",
        "  - id: shop.loyalty\n    kind: module\n    parent_id: shop\n"
        "    intent: {summary: Points.}\n"
        "    technical:\n      location: {path: src/shop/loyalty.py}\n  - id: tests.test_cart\n",
    )
    report = run(repo).report
    assert report.moved == []
    assert report.missing == []  # planned: its file doesn't have to exist yet
    assert nodes(repo)["shop.loyalty"].lifecycle_state == "planned"


def test_package_named_like_the_repo_gets_a_kind_suffix(copy_repo: CopyRepo) -> None:
    root = copy_repo("py_shop")
    (root / "intensify.yaml").write_text(render("shop", "Shop", "A tiny shop."))
    run(root)
    after = nodes(root)
    assert after["shop"].kind == "repo"
    assert after["shop-module"].kind == "module"
    assert after["shop.cart"].parent_id == "shop-module"
