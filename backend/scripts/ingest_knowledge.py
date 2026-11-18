"""Index data/knowledge/ into the Neon PGVector table (idempotent).

uv run python -m scripts.ingest_knowledge
"""

import asyncio
import sys

from app.rag.index import get_knowledge_store, reindex


async def main() -> None:
    report = await reindex(get_knowledge_store())
    print(
        f"{report.chunks} chunks: {report.added} added, {report.removed} removed, "
        f"{report.unchanged} unchanged"
    )
    for source, n in sorted(report.by_source.items()):
        print(f"  {source:24} {n}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
