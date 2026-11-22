"""FastAPI application entry point."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import (
    apps,
    case,
    chat,
    intake,
    knowledge,
    payments,
    procedures,
    reference,
    runs,
    stats,
)
from app.api.schemas import HealthOut
from app.config import Settings, get_settings
from app.deps import SessionDep, require_api_key

log = logging.getLogger(__name__)


def storage_dir(settings: Settings) -> Path:
    if settings.storage_dir:
        return Path(settings.storage_dir).expanduser()
    return Path(__file__).resolve().parents[1] / "var" / "uploads"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the LangGraph checkpointer, build the graph and resume interrupted runs."""
    settings = get_settings()
    if settings.app_env == "test" or not settings.database_url:
        yield  # tests install their own run service
        return
    from app.db.session import get_sessionmaker
    from app.graph.builder import build_graph
    from app.graph.deps import GraphDeps
    from app.graph.runner import RunService, open_checkpointer
    from app.integrations.notify import Notifier

    async with open_checkpointer(settings) as saver:
        sessionmaker = get_sessionmaker()
        deps = GraphDeps(
            sessionmaker=sessionmaker,
            evidence_dir=storage_dir(settings).parent / "evidence",
        )
        graph = build_graph(deps, saver)
        notifier = Notifier(sessionmaker, settings)
        app.state.notifier = notifier
        service = RunService(graph, sessionmaker, storage_dir(settings))
        app.state.run_service = service
        # Apps platform: pipeline events → installed apps; timers; n8n moves into an app.
        from app.apps.platform import AppPlatform

        platform = AppPlatform(sessionmaker, settings, run_service=service)
        service.on_event = platform.emit
        app.state.apps = platform
        try:
            await platform.import_env_n8n()
        except Exception:
            log.exception("could not move the n8n settings into the n8n Connector app")
        platform.start()
        app.state.assistant = build_assistant_service(sessionmaker, saver)  # None → 503
        from app.rag.embeddings import get_embeddings

        warm = asyncio.create_task(asyncio.to_thread(get_embeddings().warm_up))
        recovered = await service.recover()
        if recovered:
            log.info("resumed %d interrupted run(s)", recovered)
        yield
        warm.cancel()
        await platform.stop()


def build_assistant_service(sessionmaker: Any, saver: Any) -> Any:
    """Ask AP on the shared checkpointer (one thread per chat) and the Neon knowledge store."""
    from app.assistant.agent import build_assistant
    from app.assistant.service import AssistantService
    from app.assistant.tools import AssistantTools
    from app.llm.models import LLMNotConfiguredError, chat_model
    from app.rag.index import get_knowledge_store
    from app.rag.retriever import HybridRetriever

    tools = AssistantTools(sessionmaker, HybridRetriever(get_knowledge_store())).as_tools()
    try:
        return AssistantService(build_assistant(tools, chat_model, saver))
    except LLMNotConfiguredError as exc:
        log.warning("Ask AP is off: %s", exc)
        return None


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        lifespan=lifespan,
        title="Sinoprex Invoice API",
        version="0.1.0",
        description="Agentic AI invoice processing: PDF → reasoned decision.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key", "X-Actor"],
    )

    # Paths that stay reachable through a tunnel without the API key: the signed webhooks
    # (email intake, RazorpayX) and the health check.
    open_paths = {"/health", "/intake/email", "/webhooks/razorpayx"}

    @app.middleware("http")
    async def tunnel_guard(request: Request, call_next: Any) -> Response:
        """With no API_KEY the API is open (fine on localhost). A request that came through a
        Cloudflare tunnel (it carries `cf-ray`) is refused then, except the open paths."""
        if (
            not settings.api_key
            and request.headers.get("cf-ray")
            and request.url.path not in open_paths
        ):
            return JSONResponse(
                {"detail": "set API_KEY before exposing the API through a tunnel"},
                status_code=403,
            )
        response: Response = await call_next(request)
        return response

    @app.get("/health", response_model=HealthOut, tags=["meta"])
    async def health(session: SessionDep) -> HealthOut:
        try:
            await session.execute(text("SELECT 1"))
            db = "ok"
        except Exception:
            db = "unreachable"
        return HealthOut(status="ok", env=settings.app_env, database=db)

    protected = [Depends(require_api_key)]
    app.include_router(reference.router, dependencies=protected)
    app.include_router(knowledge.router, dependencies=protected)
    app.include_router(runs.router, dependencies=protected)
    app.include_router(stats.router, dependencies=protected)
    app.include_router(procedures.router, dependencies=protected)
    app.include_router(case.router, dependencies=protected)
    app.include_router(chat.router, dependencies=protected)
    app.include_router(intake.router, dependencies=protected)
    app.include_router(apps.router, dependencies=protected)
    app.include_router(payments.router, dependencies=protected)
    app.include_router(intake.intake_router)  # HMAC-signed by n8n, no API key
    app.include_router(payments.webhook_router)  # HMAC-signed by RazorpayX, no API key
    return app


app = create_app()
