"""Normalise values as printed on Indian invoices into canonical forms. Pure functions, no I/O.

Each function returns None when the input can't be understood — never a guess.
"""

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.rules.gst import STATE_CODES, is_valid_gstin

_CURRENCY = re.compile(r"(?i)\b(?:rs|inr|rupees)\b\.?|₹|/-")
_MONTHS = {
    m: i
    for i, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for m in names
}


def parse_amount(text: str | None) -> Decimal | None:
    """'Rs. 1,47,500.00' / '₹1,47,500' / 'INR 147500/-' / '(1,000.00)' → Decimal, 2 places."""
    if text is None:
        return None
    s = _CURRENCY.sub("", str(text)).strip()
    negative = (s.startswith("(") and s.endswith(")")) or s.startswith("-")
    s = re.sub(r"[\s,()\-]", "", s)
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        return None
    try:
        value = Decimal(s).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_quantity(text: str | None) -> Decimal | None:
    """'5,000' → 5000; '2.5 kg' → 2.5. Quantities keep their own precision."""
    if text is None:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(text))
    if not m:
        return None
    value = Decimal(m.group().replace(",", ""))
    return value.quantize(Decimal(1)) if value == value.to_integral_value() else value.normalize()


def parse_rate(text: str | None) -> Decimal | None:
    """'18%' / '18.00' / '9% + 9%' → 18. A CGST+SGST split is summed to the full GST rate."""
    if text is None:
        return None
    parts = re.findall(r"\d+(?:\.\d+)?", str(text))
    if not parts:
        return None
    total = sum((Decimal(p) for p in parts), Decimal(0))
    return total.quantize(Decimal(1)) if total == total.to_integral_value() else total.normalize()


def parse_date(text: str | None) -> date | None:
    """Indian invoices are day-first: '10-11-2026', '10/11/26', '10.11.2026', '10-Nov-2026',
    '10 November 2026', and ISO '2026-11-10'."""
    if text is None:
        return None
    s = str(text).strip().lower().replace(",", " ")
    try:
        if m := re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := re.fullmatch(r"(\d{1,2})[-/.\s](\d{1,2})[-/.\s](\d{2}|\d{4})", s):
            year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
            return date(year, int(m[2]), int(m[1]))
        if m := re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?[-/.\s]+([a-z]+)[-/.\s]+(\d{2}|\d{4})", s):
            month = _MONTHS.get(m[2])
            if month is None:
                return None
            year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
            return date(year, month, int(m[1]))
    except ValueError:  # e.g. 31-02-2026
        return None
    return None


# GSTIN positions: 0-1 digits · 2-6 letters · 7-10 digits · 11 letter · 12 alnum · 13 'Z' · 14 alnum
_DIGIT_POS = {0, 1, 7, 8, 9, 10}
_LETTER_POS = {2, 3, 4, 5, 6, 11, 13}
_TO_DIGIT = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8"}
_TO_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B"}


def normalise_gstin(text: str | None) -> tuple[str | None, bool]:
    """Return (gstin, corrected). Strips spaces/punctuation and upper-cases. If the result fails
    the checksum, tries position-aware OCR fixes (O↔0, I↔1, S↔5, B↔8, Z↔2) and accepts the fix
    only if the checksum then passes. `corrected` tells the caller to lower its confidence."""
    if text is None:
        return None, False
    g = re.sub(r"[^0-9A-Za-z]", "", str(text)).upper()
    if g.startswith("GSTIN"):
        g = g[5:]
    if len(g) != 15:
        return (g or None), False
    if is_valid_gstin(g):
        return g, False
    fixed = list(g)
    for i, ch in enumerate(fixed):
        if i in _DIGIT_POS and not ch.isdigit():
            fixed[i] = _TO_DIGIT.get(ch, ch)
        elif i in _LETTER_POS and ch.isdigit():
            fixed[i] = _TO_LETTER.get(ch, ch)
    candidate = "".join(fixed)
    if candidate != g and is_valid_gstin(candidate):
        return candidate, True
    return g, False


def normalise_po_number(text: str | None) -> str | None:
    """'PO 2026/0042', 'po-2026-42', 'P.O. No. PO-2026-0042' → 'PO-2026-0042'."""
    if text is None:
        return None
    m = re.search(
        r"(?i)P\.?\s*O\.?\s*(?:No\.?|#|:)?\s*[-:/\s]*(\d{4})\s*[-/]\s*(\d{1,5})", str(text)
    )
    if not m:
        return None
    return f"PO-{m[1]}-{int(m[2]):04d}"


_STATE_BY_NAME = {name.lower(): code for code, name in STATE_CODES.items()}


def normalise_state_code(text: str | None) -> str | None:
    """'Karnataka (29)' / '29-Karnataka' / 'Karnataka' / '29' → '29'."""
    if text is None:
        return None
    s = str(text)
    if m := re.search(r"\b(\d{2})\b", s):
        return m[1]
    for name, code in _STATE_BY_NAME.items():
        if name in s.lower():
            return code
    return None


def normalise_ifsc(text: str | None) -> str | None:
    if text is None:
        return None
    s = re.sub(r"[^0-9A-Za-z]", "", str(text)).upper()
    return s if re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", s) else None


def normalise_account(text: str | None) -> str | None:
    if text is None:
        return None
    digits = re.sub(r"\D", "", str(text))
    return digits if 6 <= len(digits) <= 20 else None


def clean_text(text: str | None) -> str | None:
    if text is None:
        return None
    s = re.sub(r"\s+", " ", str(text)).strip()
    return s or None
