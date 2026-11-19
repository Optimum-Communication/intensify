"""Price calculations."""

TAX_RATE = 0.2


def subtotal(items: list[tuple[str, int]]) -> int:
    return sum(cents for _, cents in items)


def apply_discount(cents: int, code: str) -> int:
    if code == "HALF":
        return cents // 2
    elif code == "TENOFF" and cents > 1000:
        return cents - 1000
    return cents


def with_tax(cents: int) -> int:
    return round(cents * (1 + TAX_RATE))
