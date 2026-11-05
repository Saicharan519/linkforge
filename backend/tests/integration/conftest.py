"""Fixtures for integration tests: REAL Postgres (no mocks).

Start it first with `docker compose up -d postgres`.
"""

import asyncio
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import create_engine, create_sessionmaker
from app.main import create_app
from tests.conftest import TEST_DATABASE_URL

BACKEND_DIR = Path(__file__).resolve().parents[2]

# Every table, so each test starts from an empty database.
ALL_TABLES = "users, links, click_events, link_daily_stats, api_keys"


def plain_dsn(url: str, database: str | None = None) -> str:
    """Turn a SQLAlchemy URL (postgresql+asyncpg://...) into a plain DSN for asyncpg."""
    parsed = make_url(url).set(drivername="postgresql")
    if database is not None:
        parsed = parsed.set(database=database)
    return parsed.render_as_string(hide_password=False)


def run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess[str]:
    """Run Alembic in a subprocess against `database_url`.

    A subprocess because Alembic's async env calls asyncio.run(), which cannot start inside the
    event loop that pytest-asyncio is already running.
    """
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="session", autouse=True)
def test_database() -> Iterator[None]:
    """Create the (empty) test database if it does not exist yet."""
    db_name = make_url(TEST_DATABASE_URL).database or ""
    assert re.fullmatch(r"[A-Za-z0-9_]+", db_name), f"unsafe test database name: {db_name!r}"
    # Connect to the maintenance database to be able to CREATE DATABASE.
    admin_dsn = plain_dsn(TEST_DATABASE_URL, "postgres")

    async def ensure() -> None:
        conn = await asyncpg.connect(admin_dsn, timeout=5)
        try:
            exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
            if not exists:
                await conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await conn.close()

    asyncio.run(ensure())
    yield


@pytest.fixture(scope="session", autouse=True)
def migrated_database(test_database: None) -> None:
    """Bring the test database to the latest schema once per test session (idempotent)."""
    result = run_alembic("upgrade", "head", database_url=TEST_DATABASE_URL)
    assert result.returncode == 0, result.stderr


@pytest.fixture(autouse=True)
async def clean_tables(migrated_database: None) -> None:
    """Empty every table before each test so tests cannot affect each other."""
    conn = await asyncpg.connect(plain_dsn(TEST_DATABASE_URL), timeout=5)
    try:
        await conn.execute(f"TRUNCATE {ALL_TABLES} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


@asynccontextmanager
async def client_for(settings: Settings) -> AsyncIterator[AsyncClient]:
    """An HTTP client talking to a real app instance (lifespan included) with the given settings."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Default client: app configured from the (test) environment."""
    async with client_for(Settings()) as c:
        yield c


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A database session on the test database, for arranging and asserting data directly."""
    engine = create_engine(Settings())
    sessionmaker = create_sessionmaker(engine)
    async with sessionmaker() as session:
        yield session
    await engine.dispose()
