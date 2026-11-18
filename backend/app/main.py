"""FastAPI application entry point."""

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import reference
from app.api.schemas import HealthOut
from app.config import get_settings
from app.deps import SessionDep, require_api_key


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
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
    return app


app = create_app()
