"""
VIGIL-AI Cameroun — Database Configuration
Async SQLAlchemy 2.0 with PostgreSQL + asyncpg
"""
from typing import AsyncGenerator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


def _build_async_database_url() -> str:
    """Strip unsupported SSL query parameters from the URL before handing it to asyncpg."""
    database_url = settings.DATABASE_URL
    if "?" not in database_url:
        return database_url

    parsed = urlsplit(database_url)
    query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_params.pop("sslmode", None)
    query_params.pop("ssl", None)

    new_query = urlencode(query_params)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


# ── Engine ────────────────────────────────────────────────────
# NOTE: statement_cache_size=0 disables asyncpg's client-side prepared
# statement cache. This is REQUIRED when connecting through a PgBouncer
# pooler in "transaction" mode (e.g. Supabase's pooled connection string
# on port 6543) — without it, queries intermittently fail with errors
# like "DuplicatePreparedStatementError" or "prepared statement does not
# exist" because the pooler can hand the same server-side connection to
# different clients between statements. Harmless and has no measurable
# effect on local/direct Postgres connections (e.g. Docker Compose).
try:
    database_url = _build_async_database_url()
    connect_args: dict[str, object] = {"statement_cache_size": 0}
    if "sslmode=" in settings.DATABASE_URL or "ssl=" in settings.DATABASE_URL:
        connect_args["ssl"] = "require"

    engine = create_async_engine(
        database_url,
        echo=settings.DEBUG,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args=connect_args,
    )
except Exception as e:
    # During Alembic migrations, asyncpg may not be available in sync context
    # This is safe because migrations use the sync database URL directly
    import sys
    if "alembic" in sys.modules or "alembic" in str(e):
        engine = None  # type: ignore
    else:
        raise

# ── Session Factory ───────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ── Base Model ────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Dependency ────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
