from __future__ import annotations

from pathlib import Path

import pytest

from intensify.graph import Graph
from intensify.spec import load_and_validate
from intensify.spec.edit import EditError, add_node, find, patch, save
from intensify.spec.models import LifecycleState, NodeKind


def test_graph_relations(repo: Path) -> None:
    graph = Graph.from_spec(load_and_validate(repo))
    assert [a.id for a in graph.ancestors("shop.cart.Cart.add")] == [
        "shop.cart.Cart",
        "shop.cart",
        "shop",
        "py-shop",
    ]
    assert "shop.cart.Cart.total" in graph.descendants("shop.cart")
    assert {e.to for e in graph.dependencies("shop.cart.Cart.total")} == {
        "shop.pricing.subtotal",
        "shop.pricing.apply_discount",
    }
    assert graph.tests_of("shop.cart") == ["tests.test_cart"]  # via Cart, a descendant
    assert graph.blast_radius("shop.pricing.subtotal") == ["shop.cart.Cart.total"]


def test_search_ranks_id_matches_and_filters(repo: Path) -> None:
    graph = Graph.from_spec(load_and_validate(repo))
    assert graph.search("discount")[0].id == "shop.pricing.apply_discount"
    planned = graph.search(state=LifecycleState.planned)
    assert {n.id for n in planned} == {"shop.pricing.member_discount", "shop.loyalty"}
    assert graph.search("members 5%")[0].id == "shop.pricing.member_discount"
    assert graph.search("discount", kind=NodeKind.module) == []


def test_patch_and_save_keep_comments(repo: Path) -> None:
    spec = repo / "intensify.yaml"
    spec.write_text(spec.read_text().replace("nodes:\n", "nodes:\n  # keep this comment\n", 1))
    loaded = load_and_validate(repo)
    _, node = find(loaded, "shop.pricing.subtotal")
    patch(
        node,
        {
            "intent": {"summary": "Sum of line items.", "author": "ai"},
            "governance": {"tags": ["a"]},
        },
    )
    save(loaded)
    text = spec.read_text()
    assert "  # keep this comment\n" in text
    block = text[text.index("  - id: shop.pricing.subtotal") :]
    assert "    intent:\n      summary: Sum of line items.\n      author: ai\n" in block
    assert "    governance:\n      tags: [a]\n" in block


def test_invalid_edit_is_rolled_back(repo: Path) -> None:
    before = (repo / "intensify.yaml").read_text()
    loaded = load_and_validate(repo)
    _, node = find(loaded, "shop.pricing.subtotal")
    patch(node, {"parent_id": "nope"})
    with pytest.raises(EditError, match="parent 'nope' does not exist"):
        save(loaded)
    assert (repo / "intensify.yaml").read_text() == before


def test_add_node_goes_after_parent_subtree(repo: Path) -> None:
    loaded = load_and_validate(repo)
    add_node(
        loaded,
        {
            "id": "shop.cart.clear",
            "kind": "function",
            "parent_id": "shop.cart",
            "intent": {"summary": "Empty it."},
        },
    )
    reloaded = save(loaded)
    ids = [e.node.id for e in reloaded.nodes]
    position = ids.index("shop.cart.clear")
    assert ids[position - 1] == "shop.cart.Cart.total"  # last node in shop.cart's subtree
    with pytest.raises(EditError, match="already exists"):
        add_node(
            reloaded, {"id": "shop.cart.clear", "kind": "function", "intent": {"summary": "x"}}
        )
