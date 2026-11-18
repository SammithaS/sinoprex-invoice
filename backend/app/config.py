"""Application settings, loaded from environment variables / `.env`."""

from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "test", "production"] = "local"
    database_url: str = ""

    google_api_key: str = ""
    groq_api_key: str = ""
    llm_provider: Literal["gemini", "groq"] = "gemini"  # primary for text PDFs; scans use Gemini
    gemini_model: str = "gemini-3.5-flash"
    gemini_fallback_model: str = "gemini-3.5-flash-lite"
    # Reading an invoice needs little reasoning; low thinking keeps latency down.
    gemini_thinking_level: Literal["minimal", "low", "medium", "high"] | None = "low"
    groq_model: str = "openai/gpt-oss-120b"
    llm_timeout_seconds: float = 90
    llm_max_retries: int = 2
    langsmith_api_key: str = ""
    langsmith_tracing: bool = False

    storage_backend: Literal["local", "r2"] = "local"
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""

    n8n_base_url: str = ""
    n8n_webhook_secret: str = ""

    frontend_origin: str = "http://localhost:3000"
    api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Query parameters that libpq understands but asyncpg rejects as keyword arguments.
_LIBPQ_ONLY_PARAMS = {"sslmode", "channel_binding"}


def to_async_url(url: str) -> tuple[str, dict[str, object]]:
    """Convert a libpq-style URL (as Neon hands out) into a SQLAlchemy asyncpg URL.

    Returns the URL plus `connect_args` for `create_async_engine`. `sslmode=require`
    (or stricter) becomes `ssl=True`, because asyncpg does not accept `sslmode`.
    Non-Postgres URLs (e.g. sqlite+aiosqlite in tests) pass through unchanged.
    """
    parts = urlsplit(url)
    if parts.scheme not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        return url, {}

    query = dict(parse_qsl(parts.query))
    sslmode = query.get("sslmode", "")
    kept = {k: v for k, v in query.items() if k not in _LIBPQ_ONLY_PARAMS}
    connect_args: dict[str, object] = {}
    if sslmode in {"require", "verify-ca", "verify-full"}:
        connect_args["ssl"] = True

    new = parts._replace(scheme="postgresql+asyncpg", query=urlencode(kept))
    return urlunsplit(new), connect_args
