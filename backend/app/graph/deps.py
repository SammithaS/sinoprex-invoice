"""What the graph nodes call out to. Production wiring here; tests pass replay versions."""

from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.extraction import ExtractionResult, extract_invoice
from app.extraction.models import InvoiceData, LineItem
from app.graph.explain import Explanation, explain_case
from app.rag.contract_terms import (
    ContractTermsResult,
    read_contract_terms,
    retrieve_contract_clauses,
)
from app.rag.index import get_knowledge_store
from app.rag.ingest import load_knowledge
from app.rag.retriever import HybridRetriever
from app.rules.models import Assessment, Vendor
from app.rules.policy import DEFAULT_POLICY, Policy

ExtractFn = Callable[[bytes, str | None], Awaitable[ExtractionResult]]
ContractFn = Callable[[Vendor, list[LineItem], str | None], Awaitable[ContractTermsResult]]
ExplainFn = Callable[
    [InvoiceData, Assessment, str | None, list[dict[str, Any]], dict[str, str]],
    Awaitable[Explanation],
]


async def _extract(pdf: bytes, _sample_key: str | None) -> ExtractionResult:
    return await extract_invoice(pdf)


# Contract readings keyed by the exact excerpts retrieved: contracts only change on re-index, and
# a changed section gets a new chunk id, so a hit is always a reading of the same text.
_contract_cache: OrderedDict[tuple[str, frozenset[str]], ContractTermsResult] = OrderedDict()
CONTRACT_CACHE_SIZE = 64


async def _contract_terms(
    vendor: Vendor, lines: list[LineItem], _sample_key: str | None
) -> ContractTermsResult:
    retriever = HybridRetriever(get_knowledge_store())
    hits = await retrieve_contract_clauses(retriever, vendor.id, lines)
    key = (vendor.id, frozenset(h.chunk.id for h in hits))
    if key in _contract_cache:
        _contract_cache.move_to_end(key)
        return _contract_cache[key]
    result = await read_contract_terms(vendor.id, vendor.legal_name, lines, [h.chunk for h in hits])
    if result.terms is not None:
        _contract_cache[key] = result
        if len(_contract_cache) > CONTRACT_CACHE_SIZE:
            _contract_cache.popitem(last=False)
    return result


def policy_sections() -> dict[str, str]:
    """Policy / GST text by citation, so the explanation can quote what the rules cite."""
    out: dict[str, str] = {}
    for c in load_knowledge():
        if c.doc_type in ("policy", "gst"):
            out[c.citation] = (out.get(c.citation, "") + " " + c.text).strip()
    return out


@dataclass
class GraphDeps:
    sessionmaker: async_sessionmaker[AsyncSession]
    extract: ExtractFn = _extract
    contract_terms: ContractFn = _contract_terms
    explain: ExplainFn = explain_case
    policy: Policy = field(default_factory=lambda: DEFAULT_POLICY)
    today: Callable[[], date] = date.today
    policy_text: dict[str, str] = field(default_factory=policy_sections)
