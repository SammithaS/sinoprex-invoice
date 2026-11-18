"""Stand-in for Phase 5's RAG: read `ContractTerms` straight from the contract Markdown.

Only the cited facts the rules need — agreed rates with validity, the rate-revision clause and
a "no purchase order" clause — each with its `contracts/<vendor> §x.y` citation.
"""

import re
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path

from app.rules.models import ContractRate, ContractTerms

CONTRACTS = Path(__file__).resolve().parents[2] / "data" / "knowledge" / "contracts"
_SECTION = re.compile(r"^### (\d+\.\d+) (.+)$", re.M)
_RATE = re.compile(
    r"Agreed rate: \*\*₹([\d,.]+) per (\w+)\*\* \(HSN/SAC (\d+)\), valid "
    r"(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})"
)
_VENDOR = re.compile(r"^vendor_id: (\w+)$", re.M)


def parse_contract(path: Path) -> ContractTerms:
    text = path.read_text(encoding="utf-8")
    m = _VENDOR.search(text)
    assert m, path
    source = f"contracts/{path.stem}"
    terms = ContractTerms(vendor_id=m.group(1), source=source)
    sections = list(_SECTION.finditer(text))
    for i, sec in enumerate(sections):
        end = sections[i + 1].start() if i + 1 < len(sections) else len(text)
        body = text[sec.end() : end]
        number, title = sec.group(1), sec.group(2).strip()
        citation = f"{source} §{number}"
        if rate := _RATE.search(body):
            terms.rates.append(
                ContractRate(
                    item=re.sub(r"\s+—\s+revised$", "", title),
                    rate=Decimal(rate.group(1).replace(",", "")),
                    uom=rate.group(2),
                    hsn_sac=rate.group(3),
                    valid_from=date.fromisoformat(rate.group(4)),
                    valid_to=date.fromisoformat(rate.group(5)),
                    citation=citation,
                )
            )
        elif title.lower() == "rate revisions":
            terms.rate_revision_citation = citation
        elif title.lower() == "no purchase order":
            terms.no_po_citation = citation
    return terms


@cache
def contracts_by_vendor() -> dict[str, ContractTerms]:
    terms = (parse_contract(p) for p in sorted(CONTRACTS.glob("*.md")))
    return {t.vendor_id: t for t in terms}
