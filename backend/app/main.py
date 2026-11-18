"""FastAPI application entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import knowledge, reference, runs
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

    async with open_checkpointer(settings) as saver:
        sessionmaker = get_sessionmaker()
        graph = build_graph(GraphDeps(sessionmaker=sessionmaker), saver)
        service = RunService(graph, sessionmaker, storage_dir(settings))
        app.state.run_service = service
        recovered = await service.recover()
        if recovered:
            log.info("resumed %d interrupted run(s)", recovered)
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        lifespan=lifespan,
        title="InvoiceIQ API",
        version="0.1.0",
        description="Agentic AI invoice processing: PDF → reasoned decision.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

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
    return app


app = create_app()
