import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.config import settings
from app.db.session import get_session
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
LOCAL_DB_HOSTS = {"localhost", "127.0.0.1", "::1", "db", "postgres"}


def _test_db_name() -> str:
    # Always a *new* name, so the configured dev database can never be the drop target.
    return f"{settings.postgres_db}_test"


def _ensure_safe_target() -> None:
    """Refuse to create/drop databases on anything that is not a local dev/CI server."""
    if settings.environment == "production":
        pytest.exit("Refusing to run integration tests with ENVIRONMENT=production", returncode=2)
    if (
        settings.postgres_host not in LOCAL_DB_HOSTS
        and os.environ.get("ALLOW_REMOTE_TEST_DB") != "1"
    ):
        pytest.exit(
            f"Refusing to create/drop a test database on remote host {settings.postgres_host!r}. "
            "Set ALLOW_REMOTE_TEST_DB=1 if you really mean it.",
            returncode=2,
        )


@pytest.fixture(scope="session")
async def test_database_url() -> AsyncIterator[URL]:
    """Dedicated throw-away database; dev/prod data is never touched."""
    _ensure_safe_target()
    name = _test_db_name()
    admin = create_async_engine(
        settings.database_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        try:
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
                await conn.execute(text(f'CREATE DATABASE "{name}"'))
        except (OSError, DBAPIError) as exc:
            pytest.exit(
                f"PostgreSQL is not reachable at {settings.postgres_host}:{settings.postgres_port} "
                f"({type(exc).__name__}). Start it with `docker compose up -d db` "
                "and check POSTGRES_* in .env. Use `pytest tests/unit --no-cov` to skip DB tests.",
                returncode=2,
            )
        yield settings.database_url.set(database=name)
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        await admin.dispose()


@pytest.fixture(scope="session")
async def engine(test_database_url: URL) -> AsyncIterator[AsyncEngine]:
    """Engine on the test DB with the schema built by real Alembic migrations."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["database_url"] = test_database_url
    cfg.attributes["configure_logger"] = False  # keep app/pytest logging intact
    # env.py uses asyncio.run(), so it must run outside the running event loop.
    await asyncio.to_thread(command.upgrade, cfg, "head")
    engine = create_async_engine(test_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Session inside an outer transaction rolled back after each test.
    Code under test may call commit()/rollback(): they only affect a SAVEPOINT.
    """
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def _get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_session, None)
