"""Payment gateway clients."""

from abc import ABC, abstractmethod

from ..cart import Cart, CartError
from .retry import with_retries


class Gateway(ABC):
    @abstractmethod
    def charge(self, cents: int) -> str: ...


class StripeGateway(Gateway):
    def charge(self, cents: int) -> str:
        return with_retries(lambda: f"ch_{cents}")

    def checkout(self, cart: Cart) -> str:
        if not cart.items:
            raise CartError("empty cart")
        return self.charge(cart.total())

    @staticmethod
    def currency(*codes: str, **opts) -> str:
        return codes[0]
