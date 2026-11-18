"""Phase 5: ingestion, hybrid retrieval, grounded contract terms, knowledge API."""

import hashlib
import json
import math
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient
from langchain_core.embeddings import Embeddings

from app.extraction.models import LineItem
from app.llm.schemas import ContractRateRead, ContractTermsRead
from app.rag.contract_terms import ground, read_contract_terms
from app.rag.index import get_knowledge_store, reindex
from app.rag.ingest import Chunk, chunk_doc, load_doc, load_knowledge, parse_front_matter
from app.rag.retriever import HybridRetriever, expand_query, rrf_merge
from app.rag.store import ChunkFilter, MemoryKnowledgeStore, query_terms
from app.rules.matching import contract_rate_for
from scripts.eval_rag import (
    CONTRACT_FIXTURES,
    contract_cases,
    fixed_contract_factory,
    load_questions,
    replay_contract_terms,
)
from tests.contract_fixture import contracts_by_vendor

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = ROOT / "data" / "knowledge"
INVOICES = ROOT / "data" / "test-invoices"


class HashEmbeddings(Embeddings):
    """Deterministic bag-of-words vectors: tests need no model download."""

    dim = 256

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for w in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


@pytest.fixture
async def store() -> MemoryKnowledgeStore:
    s = MemoryKnowledgeStore(HashEmbeddings())
    await reindex(s)
    return s


# --- ingestion ----------------------------------------------------------------------------------


def test_front_matter() -> None:
    meta, body = parse_front_matter("---\ntitle: X\nvendor_id: V001\n---\n# X\n")
    assert meta == {"title": "X", "vendor_id": "V001"} and body == "# X\n"


def test_chunks_are_citable_and_carry_metadata() -> None:
    chunks = load_knowledge()
    by_cite = {c.citation: c for c in chunks}
    rate = by_cite["contracts/technova §2.1"]
    assert rate.vendor_id == "V001" and rate.doc_type == "contract"
    assert "₹1,100" in rate.text and "Laptop docking station" in rate.text
    assert rate.valid_to == date(2027, 3, 31)
    assert (
        by_cite["ap-policy §3.2"].doc_type == "policy" and by_cite["ap-policy §3.2"].vendor_id == ""
    )
    assert by_cite["gst-rules §6"].heading.startswith("Credit notes")
    assert all(len(c.text) < 1000 for c in chunks)


def test_every_ground_truth_citation_is_a_chunk() -> None:
    cites = {c.citation for c in load_knowledge()}
    for path in (INVOICES / "ground-truth").glob("*.json"):
        for cite in json.loads(path.read_text(encoding="utf-8"))["expected"]["citations"]:
            assert cite in cites, (path.name, cite)


def test_chunk_ids_are_stable_and_content_addressed(tmp_path: Path) -> None:
    doc = load_doc(KNOWLEDGE / "ap-policy.md", KNOWLEDGE)
    first, again = chunk_doc(doc), chunk_doc(doc)
    assert [c.id for c in first] == [c.id for c in again]
    doc.body = doc.body.replace("at most 2%", "at most 3%")
    changed = {c.id for c in chunk_doc(doc)} - {c.id for c in first}
    assert len(changed) == 1


async def test_reindex_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / "contracts").mkdir()
    src = (KNOWLEDGE / "contracts" / "technova.md").read_text(encoding="utf-8")
    (tmp_path / "contracts" / "technova.md").write_text(src, encoding="utf-8")
    s = MemoryKnowledgeStore(HashEmbeddings())
    first = await reindex(s, tmp_path)
    assert first.added == first.chunks and first.removed == 0
    second = await reindex(s, tmp_path)
    assert second.added == 0 and second.unchanged == second.chunks
    edited = src.replace("₹1,100 per NOS", "₹1,150 per NOS")
    (tmp_path / "contracts" / "technova.md").write_text(edited, encoding="utf-8")
    third = await reindex(s, tmp_path)
    assert third.added == 1 and third.removed == 1
    assert len(await s.ids()) == third.chunks


# --- retrieval ----------------------------------------------------------------------------------


def _chunk(cid: str) -> Chunk:
    return Chunk(cid, cid, "src", cid, cid, "policy", "", None, None, 0)


def test_rrf_merge_rewards_agreement_and_gates_weak_dense_hits() -> None:
    a, b, c = _chunk("a"), _chunk("b"), _chunk("c")
    merged = rrf_merge([(a, 0.9), (b, 0.8), (c, 0.3)], [(b, 5.0)], k=5)
    assert [m.chunk.id for m in merged] == ["b", "a"]  # c: weak dense, no FTS hit → dropped
    assert merged[0].dense_rank == 2 and merged[0].sparse_rank == 1


def test_filters_translate_for_pgvector() -> None:
    assert ChunkFilter().to_langchain() is None
    assert ChunkFilter(vendor_id="V001").to_langchain() == {"vendor_id": {"$eq": "V001"}}
    both = ChunkFilter(doc_types=("contract",), vendor_id="V001").to_langchain()
    assert both == {"$and": [{"doc_type": {"$in": ["contract"]}}, {"vendor_id": {"$eq": "V001"}}]}


def test_query_terms_and_glossary() -> None:
    assert query_terms("What is the price tolerance?") == ["price", "tolerance"]
    assert "GRN" in expand_query("were the goods received?")
    assert "purchase order" in expand_query("which PO is it?")


async def test_vendor_filter_keeps_other_vendors_out(store: MemoryKnowledgeStore) -> None:
    flt = ChunkFilter(doc_types=("contract",), vendor_id="V002")
    hits = await HybridRetriever(store).search("agreed rate laptop docking station", flt=flt)
    assert hits and all(h.chunk.vendor_id == "V002" for h in hits)


async def test_irrelevant_query_returns_nothing(store: MemoryKnowledgeStore) -> None:
    assert await HybridRetriever(store).search("zzzz qqqq") == []


def _model_cached() -> bool:
    cache = Path("~/.cache/fastembed").expanduser()
    return any(cache.glob("models--Qdrant--bge-small-en-v1.5-onnx-Q*"))


@pytest.mark.skipif(not _model_cached(), reason="FastEmbed model not downloaded")
async def test_retrieval_hit_rate_with_real_embeddings() -> None:
    from app.rag.embeddings import FastEmbedEmbeddings

    s = MemoryKnowledgeStore(FastEmbedEmbeddings("BAAI/bge-small-en-v1.5", "~/.cache/fastembed"))
    await reindex(s)
    retriever = HybridRetriever(s)
    questions = load_questions()
    hits = 0
    for q in questions:
        cited = [h.citation for h in await retriever.search(q["question"], k=5)]
        hits += any(c in q["relevant"] for c in cited)
    assert hits / len(questions) >= 0.85
    contract = await retriever.search(
        "agreed rate Laptop docking station HSN 8473",
        flt=ChunkFilter(doc_types=("contract",), vendor_id="V001"),
    )
    assert contract[0].citation == "contracts/technova §2.1"


# --- grounding ----------------------------------------------------------------------------------


def _technova_clauses() -> list[Chunk]:
    return [c for c in load_knowledge() if c.source == "contracts/technova"]


def _read(**kw: str) -> ContractTermsRead:
    rate: dict[str, str] = {
        "item": "Laptop docking station",
        "hsn_sac": "8473",
        "uom": "per NOS",
        "rate": "₹1,100",
        "valid_from": "2026-04-01",
        "valid_to": "2027-03-31",
        "section": "contracts/technova §2.1",
    }
    rate.update(kw)
    return ContractTermsRead(
        rates=[ContractRateRead.model_validate(rate)],
        rate_revision_section="contracts/technova §2.5",
        payment_terms_days=30,
        payment_terms_section="contracts/technova §3.1",
    )


def test_grounded_reading_is_kept() -> None:
    terms, dropped = ground(_read(), "V001", _technova_clauses())
    assert not dropped
    (rate,) = terms.rates
    assert rate.rate == Decimal("1100.00") and rate.uom == "NOS"
    assert rate.citation == "contracts/technova §2.1"
    assert terms.rate_revision_citation == "contracts/technova §2.5"
    assert terms.payment_terms_days == 30


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"rate": "₹1,250"}, "does not appear"),
        ({"section": "contracts/technova §9.9"}, "not among the excerpts"),
        ({"section": "contracts/technova §2.2"}, "does not appear"),
        ({"valid_to": "2027-12-31"}, "validity"),
    ],
)
def test_ungrounded_rates_are_dropped(change: dict[str, str], why: str) -> None:
    terms, dropped = ground(_read(**change), "V001", _technova_clauses())
    assert not terms.rates and why in dropped[0]


def test_clause_cited_for_the_wrong_meaning_is_dropped() -> None:
    read = ContractTermsRead(
        no_po_section="contracts/technova §5.2",
        payment_terms_days=45,
        payment_terms_section="contracts/technova §3.1",
    )
    terms, dropped = ground(read, "V001", _technova_clauses())
    assert terms.no_po_citation is None and terms.payment_terms_days is None
    assert any("does not say so" in d for d in dropped)
    assert any("45 days" in d for d in dropped)


async def test_read_contract_terms_with_a_fixed_model() -> None:
    line = LineItem(line_no=1, description="Laptop docking station", hsn_sac="8473")
    result = await read_contract_terms(
        "V001", "TechNova", [line], _technova_clauses(), fixed_contract_factory(_read())
    )
    assert result.terms is not None and result.terms.rates[0].citation.endswith("§2.1")
    empty = await read_contract_terms("V404", "Nobody", [line], [], fixed_contract_factory(_read()))
    assert empty.terms is None and empty.note


# --- recorded LLM readings ------------------------------------------------------------------------

CASES = [key for key, _ in contract_cases() if (CONTRACT_FIXTURES / f"{key}.json").exists()]


def test_every_case_with_a_vendor_has_a_recording() -> None:
    assert len(CASES) == len(contract_cases()) == 24


@pytest.mark.parametrize("key", CASES)
def test_recorded_terms_cite_the_same_rate_as_the_contract(key: str) -> None:
    """RAG + LLM + grounding picks the same contract rate per invoice line as the parser."""
    result = replay_contract_terms(key)
    gt = json.loads((INVOICES / "ground-truth" / f"{key}.json").read_text(encoding="utf-8"))
    fixture = contracts_by_vendor()[gt["vendor_id"]]
    on = date.fromisoformat(gt["fields"]["invoice_date"])
    for raw in gt["fields"]["lines"]:
        line = LineItem(
            line_no=int(raw["line_no"]), description=raw["description"], hsn_sac=raw["hsn_sac"]
        )
        want = contract_rate_for(line, fixture, on)
        got = contract_rate_for(line, result.terms, on)
        got_pair = (got.citation, got.rate) if got else None
        want_pair = (want.citation, want.rate) if want else None
        assert got_pair == want_pair, (key, raw["description"], result.dropped)


# --- API ------------------------------------------------------------------------------------------


async def test_knowledge_api(client: AsyncClient) -> None:
    mem = MemoryKnowledgeStore(HashEmbeddings())
    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_knowledge_store] = lambda: mem

    res = await client.get("/knowledge")
    assert res.status_code == 200
    docs = {d["source"]: d for d in res.json()}
    assert docs["contracts/technova"]["vendor_id"] == "V001"
    assert docs["ap-policy"]["indexed_chunks"] == 0

    res = await client.post("/admin/reindex")
    assert res.status_code == 200 and res.json()["added"] == res.json()["chunks"]
    res = await client.get("/knowledge")
    assert all(d["indexed_chunks"] == d["chunks"] for d in res.json())

    res = await client.post(
        "/knowledge/search",
        json={"query": "agreed rate docking station", "vendor_id": "V001", "k": 3},
    )
    assert res.status_code == 200
    hits = res.json()
    assert hits and all(h["vendor_id"] == "V001" for h in hits)
    assert any(h["citation"] == "contracts/technova §2.1" for h in hits)
    assert (await client.post("/knowledge/search", json={"query": " "})).status_code == 422
