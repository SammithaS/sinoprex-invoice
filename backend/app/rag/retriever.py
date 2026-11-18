"""Hybrid retrieval: dense (embeddings) + sparse (full-text), merged by Reciprocal Rank Fusion.

Grounding (plan §7): a chunk is only returned if at least one signal finds it relevant — dense
cosine similarity ≥ `min_similarity`, or a full-text hit. If nothing passes, the result is empty
and callers say "nothing found" instead of guessing.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.rag.ingest import Chunk
from app.rag.store import ChunkFilter, KnowledgeStore

RRF_K = 60
CANDIDATES = 20
MIN_SIMILARITY = 0.55  # bge-small cosine; unrelated text in this corpus scores ~0.3–0.5


# AP vocabulary: people ask in words, the documents use acronyms (and vice versa).
GLOSSARY: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bgoods receipts?\b|\breceived\b|\bdelivered\b", re.I), "GRN goods receipt"),
    (re.compile(r"\bGRN\b"), "goods receipt note"),
    (re.compile(r"\bpurchase orders?\b", re.I), "PO"),
    (re.compile(r"\bPO\b"), "purchase order"),
    (re.compile(r"\bGSTIN\b"), "GST identification number"),
)


def expand_query(query: str) -> str:
    extra = [words for pattern, words in GLOSSARY if pattern.search(query)]
    return " ".join([query, *dict.fromkeys(extra)])


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float  # RRF score (higher is better)
    dense_similarity: float | None
    dense_rank: int | None
    sparse_rank: int | None

    @property
    def citation(self) -> str:
        return self.chunk.citation


def rrf_merge(
    dense: Sequence[tuple[Chunk, float]],
    sparse: Sequence[tuple[Chunk, float]],
    *,
    k: int = 5,
    min_similarity: float = MIN_SIMILARITY,
    rrf_k: int = RRF_K,
) -> list[RetrievedChunk]:
    """Fuse two ranked lists. Dense hits below `min_similarity` only count if FTS found them too."""
    by_id: dict[str, Chunk] = {}
    sim: dict[str, float] = {}
    d_rank: dict[str, int] = {}
    s_rank: dict[str, int] = {}
    for i, (c, s) in enumerate(dense):
        by_id[c.id], sim[c.id], d_rank[c.id] = c, s, i + 1
    for i, (c, _) in enumerate(sparse):
        by_id.setdefault(c.id, c)
        s_rank[c.id] = i + 1
    out: list[RetrievedChunk] = []
    for cid, chunk in by_id.items():
        relevant = cid in s_rank or sim.get(cid, 0.0) >= min_similarity
        if not relevant:
            continue
        score = sum(1.0 / (rrf_k + r) for r in (d_rank.get(cid), s_rank.get(cid)) if r)
        out.append(RetrievedChunk(chunk, score, sim.get(cid), d_rank.get(cid), s_rank.get(cid)))
    out.sort(key=lambda r: -r.score)
    return out[:k]


class HybridRetriever:
    def __init__(self, store: KnowledgeStore, min_similarity: float = MIN_SIMILARITY) -> None:
        self.store = store
        self.min_similarity = min_similarity

    async def search(
        self, query: str, *, k: int = 5, flt: ChunkFilter | None = None
    ) -> list[RetrievedChunk]:
        f = flt or ChunkFilter()
        expanded = expand_query(query)
        dense = await self.store.dense(expanded, CANDIDATES, f)
        sparse = await self.store.sparse(expanded, CANDIDATES, f)
        return rrf_merge(dense, sparse, k=k, min_similarity=self.min_similarity)
