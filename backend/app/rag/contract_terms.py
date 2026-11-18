"""`retrieve_policy` for contracts: retrieve the vendor's clauses, have the LLM read them into
`ContractTerms`, then keep only what the cited excerpt actually says.

The LLM reads; Python checks. A rate survives only if its citation is one of the retrieved
excerpts and that excerpt's text contains the same amount (and the same validity dates, when
given). Anything else is dropped with a note, so the rules never see an invented rate
(plan §7 grounding rules, policy §3.6).
"""

import asyncio
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from app.extraction.models import LineItem
from app.extraction.normalise import parse_amount
from app.llm.models import structured_model
from app.llm.prompts import CONTRACT_INSTRUCTION, CONTRACT_SYSTEM
from app.llm.schemas import ContractTermsRead
from app.rag.ingest import Chunk
from app.rag.retriever import HybridRetriever, RetrievedChunk
from app.rag.store import ChunkFilter
from app.rules.models import ContractRate, ContractTerms

ContractModelFactory = Callable[[], Runnable[list[BaseMessage], dict[str, Any]]]
_AMOUNT = re.compile(r"(?:₹|Rs\.?|INR)\s?([\d,]+(?:\.\d+)?)")
# What a clause must say to count as that kind of clause (the LLM can cite a real excerpt for
# the wrong meaning, e.g. "Invoices must quote the PO number" as a no-PO clause).
_CLAUSE_WORDING = {
    "rate revisions": re.compile(r"revis|amend|escalat", re.I),
    "no purchase order": re.compile(
        r"\bno (?:purchase order|PO)\b[^.]*\b(?:raised|required)", re.I
    ),
    "payment terms": re.compile(r"\bdays?\b", re.I),
}

# Topics retrieved for every invoice besides one query per line item.
CLAUSE_QUERIES = (
    "rate revisions amendment change of agreed rates",
    "no purchase order is raised recurring subscription",
    "payment terms due date days",
)


def default_contract_model() -> Runnable[list[BaseMessage], dict[str, Any]]:
    return structured_model(ContractTermsRead, "text")


@dataclass
class ContractTermsResult:
    terms: ContractTerms | None
    clauses: list[Chunk]  # the excerpts the LLM saw (for the case's citations table)
    dropped: list[str] = field(default_factory=list)  # ungrounded readings, with why
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    note: str | None = None


async def retrieve_contract_clauses(
    retriever: HybridRetriever,
    vendor_id: str,
    lines: Sequence[LineItem],
    k_per_query: int = 3,
) -> list[RetrievedChunk]:
    """Per-line rate queries plus standing topics, restricted to this vendor's contract."""
    flt = ChunkFilter(doc_types=("contract",), vendor_id=vendor_id)
    queries = [
        f"agreed rate {ln.description or ''} HSN {ln.hsn_sac or ''} {ln.uom or ''}".strip()
        for ln in lines
    ]
    # Queries run concurrently (each is two round trips to Neon); results merge in query order
    # so the excerpt set is the same as running them one by one.
    results = await asyncio.gather(
        *(retriever.search(q, k=k_per_query, flt=flt) for q in [*queries, *CLAUSE_QUERIES])
    )
    seen: dict[str, RetrievedChunk] = {}
    for hits in results:
        for hit in hits:
            seen.setdefault(hit.chunk.id, hit)
    return list(seen.values())


def _amounts_in(text: str) -> set[Decimal]:
    out = set()
    for m in _AMOUNT.finditer(text):
        value = parse_amount(m.group(1))
        if value is not None:
            out.add(value)
    return out


def _rate_amount(text: str) -> Decimal | None:
    """'₹1,100' / '₹230 per REAM' / '1100' → Decimal."""
    m = _AMOUNT.search(text)
    return parse_amount(m.group(1) if m else text)


def _iso(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value.strip()) if value else None
    except ValueError:
        return None


def ground(
    read: ContractTermsRead, vendor_id: str, clauses: Sequence[Chunk]
) -> tuple[ContractTerms, list[str]]:
    """Keep only facts the cited excerpt states. Returns the terms and what was dropped."""
    by_citation: dict[str, str] = {}
    for c in clauses:
        by_citation[c.citation] = by_citation.get(c.citation, "") + "\n" + c.text
    source = clauses[0].source if clauses else f"contracts/{vendor_id}"
    dropped: list[str] = []
    rates: list[ContractRate] = []
    for r in read.rates:
        cite = r.section.strip().strip("[]")
        excerpt = by_citation.get(cite)
        amount = _rate_amount(r.rate)
        if excerpt is None:
            dropped.append(f"{r.item}: cited {cite!r}, which was not among the excerpts")
            continue
        if amount is None or amount not in _amounts_in(excerpt):
            dropped.append(f"{r.item}: rate {r.rate!r} does not appear in [{cite}]")
            continue
        valid_from, valid_to = _iso(r.valid_from), _iso(r.valid_to)
        if any(d and d.isoformat() not in excerpt for d in (valid_from, valid_to)):
            dropped.append(f"{r.item}: validity {r.valid_from}–{r.valid_to} not in [{cite}]")
            continue
        rates.append(
            ContractRate(
                item=r.item,
                hsn_sac=r.hsn_sac,
                uom=re.sub(r"^per\s+", "", r.uom, flags=re.I) if r.uom else None,
                rate=amount,
                valid_from=valid_from,
                valid_to=valid_to,
                citation=cite,
            )
        )

    def section(value: str | None, what: str) -> str | None:
        if not value:
            return None
        cite = value.strip().strip("[]")
        if cite not in by_citation:
            dropped.append(f"{what}: cited {cite!r}, which was not among the excerpts")
            return None
        if not _CLAUSE_WORDING[what].search(by_citation[cite]):
            dropped.append(f"{what}: [{cite}] does not say so")
            return None
        return cite

    days = read.payment_terms_days
    pay_cite = section(read.payment_terms_section, "payment terms")
    if days is not None and not (
        pay_cite and re.search(rf"\b{days}\s+days", by_citation[pay_cite])
    ):
        dropped.append(f"payment terms: {days} days not stated in the cited excerpt")
        days = None
    return (
        ContractTerms(
            vendor_id=vendor_id,
            source=source,
            rates=rates,
            rate_revision_citation=section(read.rate_revision_section, "rate revisions"),
            no_po_citation=section(read.no_po_section, "no purchase order"),
            payment_terms_days=days,
        ),
        dropped,
    )


def _excerpts(clauses: Sequence[Chunk]) -> str:
    return "\n\n".join(f"[{c.citation}]\n{c.text}" for c in clauses)


def _reading_order(c: Chunk) -> tuple[str, list[int], int]:
    return c.source, [int(p) for p in c.section.split(".") if p.isdigit()], c.part


async def read_contract_terms(
    vendor_id: str,
    vendor_name: str,
    lines: Sequence[LineItem],
    clauses: Sequence[Chunk],
    model_factory: ContractModelFactory = default_contract_model,
) -> ContractTermsResult:
    if not clauses:
        return ContractTermsResult(
            terms=None, clauses=[], note=f"No contract clauses found for {vendor_name}."
        )
    ordered = sorted(
        clauses,
        key=lambda c: (c.source, [int(p) for p in c.section.split(".") if p.isdigit()], c.part),
    )
    items = "; ".join(f"{ln.description} (HSN {ln.hsn_sac or '—'})" for ln in lines) or "—"
    messages: list[BaseMessage] = [
        SystemMessage(CONTRACT_SYSTEM),
        HumanMessage(
            CONTRACT_INSTRUCTION.format(
                vendor=vendor_name, items=items, excerpts=_excerpts(ordered)
            )
        ),
    ]
    out = await model_factory().ainvoke(messages)
    parsed = out.get("parsed")
    if not isinstance(parsed, ContractTermsRead):
        raise RuntimeError(f"model returned no usable structure: {out.get('parsing_error')}")
    raw = out.get("raw")
    meta = getattr(raw, "response_metadata", {}) or {}
    usage = getattr(raw, "usage_metadata", None) or {}
    terms, dropped = ground(parsed, vendor_id, ordered)
    return ContractTermsResult(
        terms=terms,
        clauses=list(ordered),
        dropped=dropped,
        model=meta.get("model_name") or meta.get("model"),
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
    )


async def contract_terms_for_invoice(
    retriever: HybridRetriever,
    vendor_id: str,
    vendor_name: str,
    lines: Sequence[LineItem],
    model_factory: ContractModelFactory = default_contract_model,
) -> ContractTermsResult:
    """The full `retrieve_policy` step for contracts: retrieve → read → ground."""
    hits = await retrieve_contract_clauses(retriever, vendor_id, lines)
    return await read_contract_terms(
        vendor_id, vendor_name, lines, [h.chunk for h in hits], model_factory
    )
