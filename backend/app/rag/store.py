"""Where chunks live: PGVector on Neon (`PgKnowledgeStore`) or memory (tests, offline eval).

Both expose the same small interface — add/delete/list, dense search (cosine similarity) and
sparse search (full-text) — so the hybrid retriever and its RRF merge are shared.

The Postgres table is created and written by `langchain-postgres`' `PGVectorStore`
(id, content, embedding, metadata columns, plus a `content_tsv` tsvector it fills on insert).
Dense search goes through `PGVectorStore`; the full-text query is our own so it can OR the
query terms (`PGVectorStore`'s built-in hybrid uses `plainto_tsquery`, which ANDs them) and keep
each signal's score for the relevance gate.
"""

import math
import re
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.rag.ingest import Chunk

METADATA_COLUMNS = ("source", "section", "citation", "doc_type", "vendor_id")
TSV_COLUMN = "content_tsv"
_WORD = re.compile(r"[a-z0-9]+")
# Words too common in this corpus to help full-text search.
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "which",
        "with",
        "when",
        "who",
        "why",
        "how",
        "does",
        "do",
        "can",
        "our",
        "we",
        "acme",
        "invoice",
        "invoices",
    ]
)


@dataclass(frozen=True)
class ChunkFilter:
    doc_types: tuple[str, ...] = ()
    vendor_id: str | None = None  # restrict to one vendor's documents

    def matches(self, chunk: Chunk) -> bool:
        if self.doc_types and chunk.doc_type not in self.doc_types:
            return False
        return self.vendor_id is None or chunk.vendor_id == self.vendor_id

    def to_langchain(self) -> dict[str, Any] | None:
        parts: list[dict[str, Any]] = []
        if self.doc_types:
            parts.append({"doc_type": {"$in": list(self.doc_types)}})
        if self.vendor_id is not None:
            parts.append({"vendor_id": {"$eq": self.vendor_id}})
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else {"$and": parts}


def query_terms(query: str) -> list[str]:
    return [w for w in _WORD.findall(query.lower()) if w not in _STOP and len(w) > 1]


class KnowledgeStore(Protocol):
    async def ids(self) -> set[str]: ...
    async def add(self, chunks: Sequence[Chunk]) -> None: ...
    async def delete(self, ids: Collection[str]) -> None: ...
    async def chunks(self) -> list[Chunk]: ...
    async def dense(self, query: str, k: int, flt: ChunkFilter) -> list[tuple[Chunk, float]]: ...
    async def sparse(self, query: str, k: int, flt: ChunkFilter) -> list[tuple[Chunk, float]]: ...


# --- memory ------------------------------------------------------------------------------------


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class MemoryKnowledgeStore:
    """In-process store: exact cosine search + BM25 over the same chunks."""

    def __init__(self, embeddings: Embeddings) -> None:
        self.embeddings = embeddings
        self._chunks: dict[str, Chunk] = {}
        self._vectors: dict[str, list[float]] = {}

    async def ids(self) -> set[str]:
        return set(self._chunks)

    async def add(self, chunks: Sequence[Chunk]) -> None:
        if not chunks:
            return
        vectors = await self.embeddings.aembed_documents([c.text for c in chunks])
        for c, v in zip(chunks, vectors, strict=True):
            self._chunks[c.id] = c
            self._vectors[c.id] = v

    async def delete(self, ids: Collection[str]) -> None:
        for i in ids:
            self._chunks.pop(i, None)
            self._vectors.pop(i, None)

    async def chunks(self) -> list[Chunk]:
        return list(self._chunks.values())

    async def dense(self, query: str, k: int, flt: ChunkFilter) -> list[tuple[Chunk, float]]:
        qv = await self.embeddings.aembed_query(query)
        scored = [
            (c, _cosine(qv, self._vectors[c.id])) for c in self._chunks.values() if flt.matches(c)
        ]
        return sorted(scored, key=lambda s: -s[1])[:k]

    async def sparse(self, query: str, k: int, flt: ChunkFilter) -> list[tuple[Chunk, float]]:
        terms = query_terms(query)
        docs = [c for c in self._chunks.values() if flt.matches(c)]
        if not terms or not docs:
            return []
        tokens = {c.id: _WORD.findall(c.text.lower()) for c in docs}
        avg = sum(len(t) for t in tokens.values()) / len(docs)
        df = Counter(w for t in tokens.values() for w in set(t))
        n = len(docs)
        scored: list[tuple[Chunk, float]] = []
        for c in docs:
            tf = Counter(tokens[c.id])
            score = 0.0
            for w in terms:
                if tf[w]:
                    idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                    norm = tf[w] * 2.2 / (tf[w] + 1.2 * (0.25 + 0.75 * len(tokens[c.id]) / avg))
                    score += idf * norm
            if score > 0:
                scored.append((c, score))
        return sorted(scored, key=lambda s: -s[1])[:k]


# --- Postgres (Neon) ---------------------------------------------------------------------------


class PgKnowledgeStore:
    def __init__(self, engine: AsyncEngine, embeddings: Embeddings, table: str, dim: int) -> None:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
            raise ValueError(f"unsafe table name {table!r}")
        self.engine = engine
        self.embeddings = embeddings
        self.table = table
        self.dim = dim
        self._store: Any = None

    async def _vector_store(self) -> Any:
        if self._store is not None:
            return self._store
        from langchain_postgres import Column, PGEngine, PGVectorStore
        from langchain_postgres.v2.hybrid_search_config import HybridSearchConfig

        pg = PGEngine.from_engine(self.engine)
        hybrid = HybridSearchConfig(tsv_column=TSV_COLUMN, tsv_lang="pg_catalog.english")
        async with self.engine.connect() as conn:
            exists = (
                await conn.execute(text("SELECT to_regclass(:t)"), {"t": self.table})
            ).scalar()
        if not exists:
            await pg.ainit_vectorstore_table(
                table_name=self.table,
                vector_size=self.dim,
                id_column=Column("id", "TEXT", nullable=False),
                metadata_columns=[Column(c, "TEXT") for c in METADATA_COLUMNS],
                hybrid_search_config=hybrid,
            )
            async with self.engine.begin() as conn:
                await conn.execute(
                    text(
                        f'CREATE INDEX IF NOT EXISTS "{self.table}_tsv_idx" '
                        f'ON "{self.table}" USING GIN ("{TSV_COLUMN}")'
                    )
                )
        self._store = await PGVectorStore.create(
            pg,
            embedding_service=self.embeddings,
            table_name=self.table,
            id_column="id",
            metadata_columns=list(METADATA_COLUMNS),
            hybrid_search_config=hybrid,
        )
        return self._store

    async def ids(self) -> set[str]:
        await self._vector_store()
        async with self.engine.connect() as conn:
            rows = await conn.execute(text(f'SELECT id FROM "{self.table}"'))
            return {r[0] for r in rows}

    async def add(self, chunks: Sequence[Chunk]) -> None:
        if not chunks:
            return
        store = await self._vector_store()
        docs = [c.to_document() for c in chunks]
        vectors = await self.embeddings.aembed_documents([d.page_content for d in docs])
        await store.aadd_embeddings(
            texts=[d.page_content for d in docs],
            embeddings=vectors,
            metadatas=[d.metadata for d in docs],
            ids=[d.id for d in docs],
        )

    async def delete(self, ids: Collection[str]) -> None:
        if ids:
            store = await self._vector_store()
            await store.adelete(list(ids))

    async def chunks(self) -> list[Chunk]:
        await self._vector_store()
        cols = ", ".join(METADATA_COLUMNS)
        sql = f'SELECT id, content, langchain_metadata, {cols} FROM "{self.table}"'
        async with self.engine.connect() as conn:
            rows = (await conn.execute(text(sql))).mappings().all()
        return [self._row_chunk(r) for r in rows]

    @staticmethod
    def _row_chunk(row: Any) -> Chunk:
        meta = dict(row["langchain_metadata"] or {})
        meta.update({c: row[c] for c in METADATA_COLUMNS})
        return Chunk.from_document(
            Document(id=row["id"], page_content=row["content"], metadata=meta)
        )

    async def dense(self, query: str, k: int, flt: ChunkFilter) -> list[tuple[Chunk, float]]:
        store = await self._vector_store()
        vector = await self.embeddings.aembed_query(query)
        hits = await store.asimilarity_search_with_score_by_vector(
            vector, k=k, filter=flt.to_langchain(), hybrid_search_config=None
        )
        # Cosine distance → similarity.
        return [(Chunk.from_document(doc), 1.0 - float(dist)) for doc, dist in hits]

    async def sparse(self, query: str, k: int, flt: ChunkFilter) -> list[tuple[Chunk, float]]:
        await self._vector_store()
        terms = query_terms(query)
        if not terms:
            return []
        where = [f'"{TSV_COLUMN}" @@ q']
        params: dict[str, Any] = {"q": " | ".join(terms), "k": k}
        if flt.doc_types:
            where.append("doc_type = ANY(:doc_types)")
            params["doc_types"] = list(flt.doc_types)
        if flt.vendor_id is not None:
            where.append("vendor_id = :vendor_id")
            params["vendor_id"] = flt.vendor_id
        sql = (
            f"SELECT id, content, langchain_metadata, {', '.join(METADATA_COLUMNS)}, "
            f'ts_rank_cd("{TSV_COLUMN}", q) AS rank '
            f"FROM \"{self.table}\", to_tsquery('pg_catalog.english', :q) q "
            f"WHERE {' AND '.join(where)} ORDER BY rank DESC LIMIT :k"
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(text(sql), params)).mappings().all()
        return [(self._row_chunk(r), float(r["rank"])) for r in rows]
