"""Money helpers for rule messages."""

from decimal import ROUND_HALF_UP, Decimal

PAISA = Decimal("0.01")


def q2(value: Decimal) -> Decimal:
    return value.quantize(PAISA, rounding=ROUND_HALF_UP)


def inr(value: Decimal) -> str:
    """₹ with Indian digit grouping; paise only when non-zero (₹1,47,500 / ₹1,47,499.99)."""
    v = q2(value)
    sign = "-" if v < 0 else ""
    whole, _, frac = f"{abs(v):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{sign}₹{whole}" + (f".{frac}" if frac != "00" else "")


def pct(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def sentence(text: str) -> str:
    """Upper-case the first letter only (str.capitalize() would lower-case "IGST")."""
    return text[:1].upper() + text[1:]
