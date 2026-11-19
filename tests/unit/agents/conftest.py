from __future__ import annotations

from pathlib import Path

import pytest

from intensify.cli.init import render
from intensify.pipeline import analyze_and_write as analyze
from intensify.spec import yamlio
from tests.conftest import CopyRepo


@pytest.fixture
def repo(copy_repo: CopyRepo) -> Path:
    """The Python fixture, analyzed, with a few hand-written nodes, edges and an invariant."""
    root = copy_repo("py_shop")
    (root / "intensify.yaml").write_text(render("py-shop", "Shop", "A tiny shop."))
    analyze(root)
    spec = root / "intensify.yaml"
    data = yamlio.read(spec)
    nodes = {n["id"]: n for n in data["nodes"]}
    nodes["shop.pricing"]["intent"]["summary"] = "Price calculations."
    nodes["shop.cart"]["governance"] = yamlio.loads(
        "invariants:\n  - id: no-payments\n    text: The cart never calls payment code.\n"
        "    rule: {forbid_edge: {to: shop.payments.*}}\n"
    )
    data["nodes"].extend(
        yamlio.loads(
            "- {id: shop.pricing.member_discount, kind: function, parent_id: shop.pricing,"
            " intent: {summary: Members get 5% off.},"
            " contract: {inputs: [{name: cents, type: int}], outputs: [{type: int}]}}\n"
            "- {id: shop.loyalty, kind: module, parent_id: shop-module,"
            " intent: {summary: Loyalty points.}}\n"
        )
    )
    # `shop` is the package; the analyzer named it shop because the project id differs.
    data["nodes"][-1]["parent_id"] = "shop"
    data["edges"].append(
        yamlio.loads(
            "{from: shop.pricing.apply_discount, to: shop.loyalty, type: BLOCKS,"
            " lifecycle_state: planned}"
        )
    )
    yamlio.write(data, spec)
    return root


def implement_member_discount(root: Path, *, with_test: bool = True) -> None:
    pricing = root / "src/shop/pricing.py"
    pricing.write_text(
        pricing.read_text()
        + "\n\ndef member_discount(cents: int) -> int:\n    return cents * 95 // 100\n"
    )
    if with_test:
        test = root / "tests/test_cart.py"
        test.write_text(
            test.read_text() + "\n\ndef test_member_discount():\n"
            "    from shop.pricing import member_discount\n\n"
            "    assert member_discount(100) == 95\n"
        )
