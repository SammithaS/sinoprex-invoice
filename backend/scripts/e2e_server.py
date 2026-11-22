"""Backend for the Playwright end-to-end tests: the real API on a throwaway stack.

    APP_ENV=test uv run uvicorn scripts.e2e_server:app --port 8020

Same FastAPI app and LangGraph pipeline as production, but: a fresh SQLite database with the
seed data (in a temp folder, gone when the process exits), an in-memory checkpointer, recorded
LLM outputs replayed for the 25 samples (no API calls, deterministic, fast), template
explanations, and an in-memory knowledge index. Neon, n8n and Gemini are never touched. Ask AP
is off (it needs a live model), so the chat page shows its error state.
"""

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    import httpx

    from app.apps.mail import IncomingEmail, OutgoingEmail
    from app.apps.platform import AppPlatform
    from app.graph.runner import RunService

os.environ["APP_ENV"] = "test"  # create_app's lifespan then leaves the services to us
os.environ["N8N_BASE_URL"] = ""  # no notifications from test runs
os.environ["N8N_WEBHOOK_SECRET"] = ""
os.environ["API_KEY"] = ""  # local throwaway stack: no key, whatever backend/.env says
# Apps: a throwaway encryption key; email and HTTP go to fakes (nothing leaves the machine).
os.environ["SECRETS_KEY"] = __import__("cryptography.fernet").fernet.Fernet.generate_key().decode()

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


class _FakeMail:
    async def send(self, message: "OutgoingEmail") -> None:
        return None

    async def fetch(self, query: str, limit: int) -> "list[IncomingEmail]":
        return []

    async def mark_seen(self, uids: list[str]) -> None:
        return None

    async def check(self) -> None:
        return None


def _fake_http(request: "httpx.Request") -> "httpx.Response":
    """RazorpayX test mode in miniature (payouts are processed at once); anything else: ok."""
    import httpx

    if request.url.host != "api.razorpay.com":
        return httpx.Response(200, text="ok")
    path = request.url.path.removeprefix("/v1")
    body = json.loads(request.content) if request.content else {}
    if path == "/contacts":
        return httpx.Response(200, json={"id": f"cont_{body['reference_id']}"})
    if path == "/fund_accounts":
        return httpx.Response(200, json={"id": f"fa_{body['contact_id'][5:]}"})
    if path == "/payouts":
        key = request.headers["X-Payout-Idempotency"]
        ref = hashlib.sha1(key.encode()).hexdigest()[:10].upper()
        return httpx.Response(
            200, json={"id": f"pout_{ref}", "status": "processed", "utr": f"UTR{ref}"}
        )
    return httpx.Response(200, json={"items": []})


def _apps_platform(service: "RunService") -> "AppPlatform":
    import httpx

    from app.apps.context import Transports
    from app.apps.platform import AppPlatform

    fake = _FakeMail()
    transports = Transports(
        mailer=lambda v: fake,
        mailbox=lambda v: fake,
        http=httpx.MockTransport(_fake_http),
    )
    platform = AppPlatform(
        service.sessionmaker, get_settings(), transports=transports, run_service=service
    )
    service.on_event = platform.emit
    return platform


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
    app.state.apps = _apps_platform(service)
    yield
    await app.state.apps.stop()
    await service.wait_all()
    await engine.dispose()
    await asyncio.sleep(0)


app.router.lifespan_context = _lifespan
