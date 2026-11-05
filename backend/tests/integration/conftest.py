"""Fixtures for integration tests: REAL Postgres (no mocks).

Start it first with `docker compose up -d postgres`.
"""

import asyncio
import re
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url

from app.config import Settings
from app.main import create_app
from tests.conftest import TEST_DATABASE_URL


@pytest.fixture(scope="session", autouse=True)
def test_database() -> Iterator[None]:
    """Create the (empty) test database if it does not exist yet."""
    url = make_url(TEST_DATABASE_URL)
    db_name = url.database or ""
    assert re.fullmatch(r"[A-Za-z0-9_]+", db_name), f"unsafe test database name: {db_name!r}"
    # Connect to the maintenance database to be able to CREATE DATABASE.
    admin_dsn = url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )

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


ClientFactory = Callable[[Settings], AbstractAsyncContextManager[AsyncClient]]


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
