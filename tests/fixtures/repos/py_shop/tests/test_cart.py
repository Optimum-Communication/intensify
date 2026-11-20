from shop.cart import Cart
from shop.pricing import apply_discount


def test_add():
    cart = Cart("ana")
    cart.add("sku", 100)
    assert cart.total() == 100


def test_discount():
    assert apply_discount(1000, "HALF") == 500
