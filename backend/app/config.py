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
    # Outer retries for 503/429 when every provider in the fallback chain is briefly down.
    llm_retry_delays: list[float] = [8, 30]
    # RAG: local FastEmbed model (no API, no rate limits) and the PGVector table it fills.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    embedding_cache_dir: str = "~/.cache/fastembed"
    knowledge_dir: str = ""  # default: <repo>/data/knowledge
    knowledge_table: str = "knowledge_chunks"

    langsmith_api_key: str = ""
    langsmith_tracing: bool = False

    storage_backend: Literal["local", "r2"] = "local"
    storage_dir: str = ""  # local uploads; default backend/var/uploads
    # Mock ERP honours a sample's `erp_fault` (demo of the read-back check).
    erp_demo_faults: bool = True
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""

    # n8n (optional). Base URL of the n8n instance, e.g. https://<name>.app.n8n.cloud; webhooks
    # both ways are HMAC-SHA256 signed with this shared secret (empty = intake and notices off).
    n8n_base_url: str = ""
    n8n_webhook_secret: str = ""
    n8n_review_path: str = "invoiceiq-review"
    n8n_vendor_path: str = "invoiceiq-vendor-followup"
    intake_max_attachments: int = 5
    signature_tolerance_seconds: int = 300
    app_url: str = ""  # where deep links in notifications point; default: frontend_origin
    # Apps platform: Fernet key(s) that encrypt app secrets (comma-separated; the first encrypts,
    # all decrypt, for rotation). Server-side only; never stored in the database.
    secrets_key: str = ""
    # How often the app scheduler checks timers (seconds).
    apps_tick_seconds: float = 20

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
