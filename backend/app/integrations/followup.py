"""Vendor follow-up email text from the templates in `vendor-faq.md` (plan §12 workflow 3).

The template for the case's first vendor-facing exception is filled from what is known (vendor,
invoice number and date, the exception's variance details). If a placeholder can't be filled, a
plain message built from the exception's own summary is used instead — never a half-filled
template with `{braces}` left in it.
"""

import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.rag.ingest import default_knowledge_dir

FAQ = "vendor-faq"
# Exception category → FAQ section with the template to send. ERP_MISMATCH is internal.
SECTION_BY_CATEGORY = {
    "NO_PO": "1",
    "TAX_MISMATCH": "3",
    "PRICE_VARIANCE": "5",
    "QTY_VARIANCE": "6",
    "OVER_PO_BALANCE": "6",
    "NO_RECEIPT": "6",
    "BANK_CHANGE": "7",
    "LOW_CONFIDENCE": "8",
    "DUPLICATE_SUSPECT": "9",
}
# The rules' variance keys, under the names the FAQ templates use.
ALIASES = {
    "invoiced_rate": "billed_rate",
    "agreed_rate": "reference_rate",
    "source": "reference_source",
}
_SECTION = re.compile(r"^## (\d+)\. (.+)$")
_PLACEHOLDER = re.compile(r"\{([a-z_0-9]+)\}")


@lru_cache(maxsize=1)
def templates(path: Path | None = None) -> dict[str, tuple[str, str]]:
    """section → (title, template text) from the FAQ's blockquotes."""
    text = (path or default_knowledge_dir() / f"{FAQ}.md").read_text(encoding="utf-8")
    out: dict[str, tuple[str, str]] = {}
    section: tuple[str, str] | None = None
    quote: list[str] = []
    for line in [*text.splitlines(), "## 0. end"]:
        m = _SECTION.match(line)
        if m:
            if section and quote:
                out[section[0]] = (section[1], " ".join(quote))
            section, quote = (m.group(1), m.group(2)), []
        elif line.startswith(">") and section:
            quote.append(line.lstrip("> ").strip())
    return out


def _amount(value: str) -> str:
    """'1250.00' → '1,250'; '1234567.5' → '12,34,567.50' (Indian grouping)."""
    try:
        d = Decimal(value)
    except (InvalidOperation, ValueError):
        return value
    whole, frac = f"{abs(d):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    text = ",".join([g for g in [head, *groups] if g] + [tail]) if head or groups else tail
    return ("-" if d < 0 else "") + text + ("" if frac == "00" else f".{frac}")


def _fill(template: str, values: dict[str, str]) -> str | None:
    missing = [k for k in _PLACEHOLDER.findall(template) if not values.get(k)]
    if missing:
        return None
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def compose(
    *,
    vendor_name: str,
    invoice_number: str | None,
    invoice_date: str | None,
    exceptions: list[dict[str, Any]],
    low_confidence_fields: list[str],
    reviewer_comment: str | None,
    lines: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """{subject, message, citation, category} for the vendor email."""
    vendor_facing = [e for e in exceptions if e.get("category") in SECTION_BY_CATEGORY]
    first = vendor_facing[0] if vendor_facing else None
    inv = invoice_number or "(no number)"
    variance = {k: str(v) for k, v in ((first or {}).get("variance") or {}).items()}
    for name, key in ALIASES.items():
        if key in variance:
            variance.setdefault(name, variance[key])
    for key in ("invoiced_rate", "agreed_rate", "over_total"):
        if key in variance:
            variance[key] = _amount(variance[key])
    line = next((ln for ln in lines or [] if str(ln.get("line_no")) == variance.get("line")), None)
    values = {
        "vendor_name": vendor_name,
        "invoice_number": inv,
        "invoice_date": invoice_date or "",
        "fields": ", ".join(f.replace("_", " ") for f in low_confidence_fields),
        "item": str((line or {}).get("description") or ""),
        **variance,
    }
    citation: str | None = None
    message: str | None = None
    if first is not None:
        section = SECTION_BY_CATEGORY[first["category"]]
        found = templates().get(section)
        if found:
            message = _fill(found[1], values)
            citation = f"{FAQ} §{section}" if message is not None else None
    if message is None:
        dated = f" dated {invoice_date}" if invoice_date else ""
        issue = (first or {}).get("summary") or "we need some information before we can process it"
        message = (
            f"Dear {vendor_name}, thank you for invoice {inv}{dated}. We could not process it yet: "
            f"{issue.rstrip('.')}. Please reply with a corrected invoice or the missing details."
        )
    if reviewer_comment and reviewer_comment.strip():
        message += f"\n\nNote from our AP team: {reviewer_comment.strip()}"
    return {
        "subject": f"Invoice {inv}: information needed",
        "message": message,
        "citation": citation,
        "category": (first or {}).get("category"),
    }
