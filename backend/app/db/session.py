"""Async SQLAlchemy engine and session factory."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings, to_async_url


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set. Copy backend/.env.example to backend/.env.")
    url, connect_args = to_async_url(settings.database_url)
    # Neon scales to zero and closes idle connections: pre-ping and recycle.
    return create_async_engine(url, connect_args=connect_args, pool_pre_ping=True, pool_recycle=300)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
