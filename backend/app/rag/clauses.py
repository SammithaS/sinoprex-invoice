"""The vendor-contract sections behind non-price checks, found by topic.

A blocked vendor, a changed bank account, the wrong GST heads or a missing GRN are decided from
the vendor master, the GST rules and the PO — but the vendor's own contract usually says the
same thing, and a reviewer wants to see that clause. This maps topic → the contract section that
covers it, read straight from the knowledge files (the same text the index is built from), so a
check only ever cites a clause that exists in that vendor's contract.
"""

import re
from functools import lru_cache

from app.rag.ingest import Chunk, load_knowledge

# topic → alternatives in order of preference: (heading pattern, text the section must contain)
TOPICS: dict[str, list[tuple[re.Pattern[str], re.Pattern[str] | None]]] = {
    # Prefer the clause on *changing* bank details; else the one naming the account.
    "bank": [
        (re.compile(r"\bbank\b", re.I), re.compile(r"\bchange", re.I)),
        (re.compile(r"\bbank\b", re.I), None),
    ],
    "tax": [(re.compile(r"\btax\b|\bGST\b", re.I), re.compile(r"\b(CGST|SGST|IGST)\b"))],
    "termination": [(re.compile(r"terminat", re.I), None)],
    # A section that makes payment depend on the goods receipt (not one that waives it).
    "receipt": [(re.compile(r".", re.S), re.compile(r"\bonly against a GRN\b", re.I))],
}


@lru_cache(maxsize=1)
def _contract_chunks() -> dict[str, list[Chunk]]:
    out: dict[str, list[Chunk]] = {}
    for c in load_knowledge():
        if c.doc_type == "contract" and c.vendor_id and c.section:
            out.setdefault(c.vendor_id, []).append(c)
    return out


def vendor_mentions(query: str) -> list[str]:
    """Vendor ids whose contract is named in the query by its short name ('Deccan', 'Zenith'),
    taken from the corpus itself (contracts/<name>.md)."""
    words = set(re.findall(r"[a-z0-9]+", query.lower()))
    found: list[str] = []
    for vendor_id, chunks in _contract_chunks().items():
        slug = chunks[0].source.rsplit("/", 1)[-1].lower()
        if slug in words:
            found.append(vendor_id)
    return found


def topic_citations(vendor_id: str | None) -> dict[str, str]:
    """{'bank': 'contracts/rangoli §5.1', …} for the topics this vendor's contract covers."""
    chunks = _contract_chunks().get(vendor_id or "", [])
    found: dict[str, str] = {}
    for topic, alternatives in TOPICS.items():
        for heading, text in alternatives:
            hit = next(
                (
                    c
                    for c in chunks
                    if heading.search(c.heading) and (text is None or text.search(c.text))
                ),
                None,
            )
            if hit is not None:
                found[topic] = hit.citation
                break
    return found
