"""Knowledge base: list documents, hybrid search, re-index (plan §11 `POST /admin/reindex`)."""

from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas import (
    KnowledgeDocOut,
    KnowledgeHitOut,
    KnowledgeSearchIn,
    ReindexOut,
)
from app.rag.index import get_knowledge_store, reindex
from app.rag.ingest import default_knowledge_dir, load_doc, load_knowledge
from app.rag.retriever import HybridRetriever
from app.rag.store import ChunkFilter, KnowledgeStore

router = APIRouter(tags=["knowledge"])
StoreDep = Annotated[KnowledgeStore, Depends(get_knowledge_store)]


@router.get("/knowledge", response_model=list[KnowledgeDocOut])
async def list_documents(store: StoreDep) -> list[KnowledgeDocOut]:
    root = default_knowledge_dir()
    chunks = load_knowledge(root)
    wanted = Counter(c.source for c in chunks)
    indexed_ids = await store.ids()
    indexed = Counter(c.source for c in chunks if c.id in indexed_ids)
    out = []
    for path in sorted(root.rglob("*.md")):
        doc = load_doc(path, root)
        out.append(
            KnowledgeDocOut(
                source=doc.source,
                title=doc.title,
                doc_type=doc.doc_type,
                vendor_id=doc.meta.get("vendor_id"),
                chunks=wanted[doc.source],
                indexed_chunks=indexed[doc.source],
            )
        )
    return out


@router.post("/knowledge/search", response_model=list[KnowledgeHitOut])
async def search(body: KnowledgeSearchIn, store: StoreDep) -> list[KnowledgeHitOut]:
    if not body.query.strip():
        raise HTTPException(status_code=422, detail="query is empty")
    flt = ChunkFilter(doc_types=tuple(body.doc_types), vendor_id=body.vendor_id)
    hits = await HybridRetriever(store).search(body.query, k=min(body.k, 20), flt=flt)
    return [
        KnowledgeHitOut(
            citation=h.citation,
            heading=h.chunk.heading,
            doc_type=h.chunk.doc_type,
            vendor_id=h.chunk.vendor_id or None,
            text=h.chunk.text,
            score=h.score,
            dense_similarity=h.dense_similarity,
        )
        for h in hits
    ]


@router.post("/admin/reindex", response_model=ReindexOut, tags=["admin"])
async def reindex_knowledge(store: StoreDep) -> ReindexOut:
    report = await reindex(store)
    return ReindexOut(**vars(report))
