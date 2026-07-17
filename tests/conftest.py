"""
VIGIL-AI Cameroun — Pytest Configuration & Fixtures
Uses a separate test database (vigilai_test_db) with transactional rollback per test.
"""
import asyncio
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.core.security import hash_password, create_access_token
from app.models.user import Role, User


# ── Test Database Setup ───────────────────────────────────────
def _test_database_url() -> str:
    """Derive the test-database URL by suffixing the database *path segment*
    only (a plain str.replace corrupts the URL when the DB is named
    'postgres', which also appears in the scheme and username)."""
    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(settings.DATABASE_URL)
    db_name = (parsed.path or "/").lstrip("/") or "vigilai_db"
    return urlunsplit(
        (parsed.scheme, parsed.netloc, f"/{db_name}_test", parsed.query, parsed.fragment)
    )


@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# NOTE: engine creation is lazy and NOT autouse — pure unit tests
# (heuristics, engine fallback) must run without a PostgreSQL instance.
# Any fixture that touches the DB depends on test_engine explicitly.
@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """Create the test engine + schema for the session, drop after."""
    engine = create_async_engine(_test_database_url(), echo=False, poolclass=None)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional database session for each test (rolled back after)."""
    async with test_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP test client with overridden DB dependency."""
    async def _get_test_db():
        yield db_session

    app.dependency_overrides[get_db] = _get_test_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()


# ── Role & User Fixtures ──────────────────────────────────────
@pytest_asyncio.fixture
async def roles(db_session: AsyncSession) -> dict[str, Role]:
    role_map = {}
    for name in ["admin", "analyst", "viewer"]:
        role = Role(name=name, description=f"{name} role")
        db_session.add(role)
        await db_session.flush()
        role_map[name] = role
    await db_session.commit()
    return role_map


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession, roles: dict[str, Role]) -> User:
    user = User(
        email="admin@test.cm",
        password_hash=hash_password("AdminTest123!"),
        full_name="Test Admin",
        role_id=roles["admin"].id,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user, attribute_names=["role"])
    return user


@pytest_asyncio.fixture
async def analyst_user(db_session: AsyncSession, roles: dict[str, Role]) -> User:
    user = User(
        email="analyst@test.cm",
        password_hash=hash_password("AnalystTest123!"),
        full_name="Test Analyst",
        role_id=roles["analyst"].id,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user, attribute_names=["role"])
    return user


@pytest_asyncio.fixture
async def viewer_user(db_session: AsyncSession, roles: dict[str, Role]) -> User:
    user = User(
        email="viewer@test.cm",
        password_hash=hash_password("ViewerTest123!"),
        full_name="Test Viewer",
        role_id=roles["viewer"].id,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user, attribute_names=["role"])
    return user


# ── Auth Header Fixtures ──────────────────────────────────────
@pytest.fixture
def admin_headers(admin_user: User) -> dict[str, str]:
    token = create_access_token(admin_user.id, "admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def analyst_headers(analyst_user: User) -> dict[str, str]:
    token = create_access_token(analyst_user.id, "analyst")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def viewer_headers(viewer_user: User) -> dict[str, str]:
    token = create_access_token(viewer_user.id, "viewer")
    return {"Authorization": f"Bearer {token}"}
