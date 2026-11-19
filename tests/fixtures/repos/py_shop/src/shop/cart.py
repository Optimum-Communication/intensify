"""Shopping cart."""

from __future__ import annotations

from shop import pricing
from shop._internal import audit
from shop.pricing import apply_discount as discount


class CartError(Exception):
    """Raised for invalid cart operations."""


class Cart:
    """A customer's cart."""

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.items: list[tuple[str, int]] = []

    def add(self, sku: str, cents: int, qty: int = 1) -> None:
        if qty <= 0:
            raise CartError("quantity must be positive")
        for _ in range(qty):
            self.items.append((sku, cents))
        audit("add", sku)

    def total(self, code: str | None = None) -> int:
        subtotal = pricing.subtotal(self.items)
        return discount(subtotal, code) if code else subtotal

    def _debug(self) -> str:
        return repr(self.items)
