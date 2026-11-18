"""Backend for the Playwright end-to-end tests: the real API on a throwaway stack.

    APP_ENV=test uv run uvicorn scripts.e2e_server:app --port 8020

Same FastAPI app and LangGraph pipeline as production, but: a fresh SQLite database with the
seed data (in a temp folder, gone when the process exits), an in-memory checkpointer, recorded
LLM outputs replayed for the 25 samples (no API calls, deterministic, fast), template
explanations, and an in-memory knowledge index. Neon, n8n and Gemini are never touched. Ask AP
is off (it needs a live model), so the chat page shows its error state.
"""

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

os.environ["APP_ENV"] = "test"  # create_app's lifespan then leaves the services to us
os.environ["N8N_BASE_URL"] = ""  # no notifications from test runs
os.environ["N8N_WEBHOOK_SECRET"] = ""
os.environ["API_KEY"] = ""  # local throwaway stack: no key, whatever backend/.env says

from app.config import get_settings
from app.db.session import get_session
from app.erp import MockErp
from app.main import create_app
from app.rag.index import get_knowledge_store, reindex
from app.rag.store import MemoryKnowledgeStore
from scripts.eval import make_service

get_settings.cache_clear()
_tmp = tempfile.TemporaryDirectory(prefix="invoiceiq-e2e-", ignore_cleanup_errors=True)
app = create_app()


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    service, engine = await make_service(live=False, workdir=Path(_tmp.name))
    maker = service.sessionmaker

    async def session() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    from app.rag.embeddings import get_embeddings

    store = MemoryKnowledgeStore(get_embeddings())
    await reindex(store)
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_knowledge_store] = lambda: store
    app.state.run_service = service
    app.state.erp = MockErp(maker)
    yield
    await service.wait_all()
    await engine.dispose()
    await asyncio.sleep(0)


app.router.lifespan_context = _lifespan
