"""(Re-)index the knowledge base into a store — idempotent by chunk id."""

from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.rag.embeddings import get_embeddings
from app.rag.ingest import Chunk, load_knowledge
from app.rag.store import KnowledgeStore, PgKnowledgeStore


@dataclass
class IndexReport:
    chunks: int
    added: int
    removed: int
    unchanged: int
    by_source: dict[str, int] = field(default_factory=dict)


async def reindex(store: KnowledgeStore, root: Path | None = None) -> IndexReport:
    wanted: list[Chunk] = load_knowledge(root)
    existing = await store.ids()
    wanted_ids = {c.id for c in wanted}
    stale = existing - wanted_ids
    new = [c for c in wanted if c.id not in existing]
    await store.delete(stale)
    await store.add(new)
    return IndexReport(
        chunks=len(wanted),
        added=len(new),
        removed=len(stale),
        unchanged=len(wanted) - len(new),
        by_source=dict(Counter(c.source for c in wanted)),
    )


@lru_cache
def get_knowledge_store() -> KnowledgeStore:
    """The Neon-backed store (shares the app's async engine)."""
    from app.db.session import get_engine

    s = get_settings()
    return PgKnowledgeStore(get_engine(), get_embeddings(), s.knowledge_table, s.embedding_dim)
