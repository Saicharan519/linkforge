from httpx import AsyncClient

from app.config import Settings
from tests.conftest import TEST_DATABASE_URL
from tests.integration.conftest import client_for


async def test_app_boots(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz_ok_with_postgres(client: AsyncClient) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "postgres": "ok", "redis": "ok"}


async def test_readyz_fails_when_db_unreachable() -> None:
    # Port 1 on localhost: nothing listens there, so the connection is refused immediately.
    settings = Settings(database_url="postgresql+asyncpg://nobody:nopass@127.0.0.1:1/none")
    async with client_for(settings) as client:
        # The app still boots and serves liveness...
        assert (await client.get("/healthz")).status_code == 200
        # ...but readiness reports 503 instead of crashing or hanging.
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable", "postgres": "down", "redis": "ok"}


async def test_docs_disabled_by_default() -> None:
    settings = Settings(database_url=TEST_DATABASE_URL, enable_docs=False)
    async with client_for(settings) as client:
        assert (await client.get("/api/docs")).status_code == 404
        assert (await client.get("/api/openapi.json")).status_code == 404


async def test_docs_enabled_flag() -> None:
    settings = Settings(database_url=TEST_DATABASE_URL, enable_docs=True)
    async with client_for(settings) as client:
        resp = await client.get("/api/openapi.json")
    assert resp.status_code == 200
    assert "/healthz" in resp.json()["paths"]
