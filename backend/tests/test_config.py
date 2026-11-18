from app.config import to_async_url


def test_neon_url_converted_for_asyncpg() -> None:
    url, args = to_async_url(
        "postgresql://user:pw@ep-cool-1.ap-southeast-1.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )
    assert url == "postgresql+asyncpg://user:pw@ep-cool-1.ap-southeast-1.aws.neon.tech/neondb"
    assert args == {"ssl": True}


def test_plain_url_without_ssl() -> None:
    url, args = to_async_url("postgres://u:p@localhost:5432/app")
    assert url == "postgresql+asyncpg://u:p@localhost:5432/app"
    assert args == {}


def test_non_postgres_url_passthrough() -> None:
    assert to_async_url("sqlite+aiosqlite:///:memory:") == ("sqlite+aiosqlite:///:memory:", {})
