from __future__ import annotations

from pathlib import Path

from intensify.context import build, render_markdown, to_dict
from intensify.spec import load_and_validate, yamlio
from intensify.verify import verify
from tests.unit.agents.conftest import implement_member_discount


def test_context_sections(repo: Path) -> None:
    bundle = build(load_and_validate(repo), "shop.pricing.apply_discount", include_source=True)
    assert [d.node_id for d in bundle.dependents] == ["shop.cart.Cart.total"]
    assert {(f.node_id, f.relation) for f in bundle.future} == {
        ("shop.pricing.member_discount", "planned sibling"),
        ("shop.loyalty", "blocked by this node"),
    }
    assert [t.id for t in bundle.tests] == ["tests.test_cart"]
    assert bundle.excerpts["shop.pricing.apply_discount"].startswith("def apply_discount(")
    text = render_markdown(bundle)
    assert "## Future capabilities (planned, not part of this task)" in text
    assert "`shop.cart.Cart.total` (calls this;" in text
    assert "intensify verify shop.pricing.apply_discount" in text


def test_context_inherits_invariants_and_handles_planned_nodes(repo: Path) -> None:
    loaded = load_and_validate(repo)
    add = build(loaded, "shop.cart.Cart.add")
    assert [(owner, inv.id) for owner, inv in add.invariants] == [("shop.cart", "no-payments")]
    planned = render_markdown(build(loaded, "shop.pricing.member_discount"))
    assert "Not implemented yet." in planned
    assert "Its parent lives at `src/shop/pricing.py`" in planned
    assert "- input `cents`: `int`" in planned


def test_budget_drops_low_priority_sections_first(repo: Path) -> None:
    bundle = build(load_and_validate(repo), "shop.pricing.apply_discount", include_source=True)
    full = render_markdown(bundle)
    small = render_markdown(
        build(load_and_validate(repo), "shop.pricing.apply_discount", include_source=True),
        budget=150,
    )
    assert len(small) < len(full)
    assert "## Intent" in small
    assert "## Contract" in small
    assert "## Current code" not in small
    assert "_Trimmed to fit the budget:" in small


def test_context_json(repo: Path) -> None:
    data = to_dict(build(load_and_validate(repo), "shop.cart.Cart.total"))
    assert data["target"]["id"] == "shop.cart.Cart.total"
    assert {d["id"] for d in data["dependencies"]} == {
        "shop.pricing.subtotal",
        "shop.pricing.apply_discount",
    }


def test_verify_planned_node_end_to_end(repo: Path) -> None:
    result = verify(repo, "shop.pricing.member_discount")
    assert not result.ok
    assert result.checks[0].name == "code exists"
    assert not result.checks[0].ok

    implement_member_discount(repo, with_test=False)
    result = verify(repo, "shop.pricing.member_discount")
    assert {c.name: c.ok for c in result.checks} == {
        "code exists": True,
        "contract matches": True,
        "invariants hold": True,
        "has tests": False,
        "tests pass": True,
    }
    assert verify(repo, "shop.pricing.member_discount", require_tests=False, dry_run=True).ok

    implement_member_discount(repo, with_test=True)
    pricing = repo / "src/shop/pricing.py"
    pricing.write_text(
        pricing.read_text().replace(
            "\n\ndef member_discount(cents: int) -> int:\n    return cents * 95 // 100\n", "", 1
        )
    )
    result = verify(repo, "shop.pricing.member_discount")
    assert result.ok, result.checks
    assert result.promoted
    node = next(
        e.node for e in load_and_validate(repo).nodes if e.node.id == "shop.pricing.member_discount"
    )
    assert node.lifecycle_state == "active"
    assert node.technical is not None
    assert node.technical.location is not None
    assert node.technical.location.symbol == "member_discount"


def test_verify_catches_contract_and_invariant_problems(repo: Path) -> None:
    implement_member_discount(repo)
    pricing = repo / "src/shop/pricing.py"
    pricing.write_text(
        pricing.read_text().replace(
            "def member_discount(cents: int)", "def member_discount(cents: float)"
        )
    )
    result = verify(repo, "shop.pricing.member_discount", dry_run=True)
    [contract] = [c for c in result.checks if c.name == "contract matches"]
    assert not contract.ok
    assert "'int' in the spec but 'float' in the code" in contract.detail

    cart = repo / "src/shop/cart.py"
    cart.write_text(
        cart.read_text()
        .replace(
            "from shop._internal import audit",
            "from shop._internal import audit\nfrom shop.payments.retry import with_retries",
        )
        .replace(
            '        audit("add", sku)',
            '        audit("add", sku)\n        with_retries(lambda: None)',
        )
    )
    result = verify(repo, "shop.cart.Cart.add", dry_run=True)
    [invariants] = [c for c in result.checks if c.name == "invariants hold"]
    assert not invariants.ok
    assert "no-payments" in invariants.detail
    # Not involved in the violation (and untested: the fixture only tests through instances).
    assert verify(repo, "shop.cart.Cart.total", require_tests=False, dry_run=True).ok


def test_verify_runs_the_test_command(repo: Path) -> None:
    implement_member_discount(repo)
    spec = repo / "intensify.yaml"
    data = yamlio.read(spec)
    data["config"]["verify"] = {
        "test_command": "python -c \"import sys; print('ran', sys.argv[1:]); sys.exit(3)\" {tests}"
    }
    yamlio.write(data, spec)
    result = verify(repo, "shop.pricing.member_discount", dry_run=True)
    [tests] = [c for c in result.checks if c.name == "tests pass"]
    assert not tests.ok
    assert "exited 3" in tests.detail
    assert "ran ['tests/test_cart.py']" in tests.detail
    assert verify(repo, "shop.pricing.member_discount", run_tests=False, dry_run=True).ok
