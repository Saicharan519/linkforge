"""Redis integration (task P2.1) against the REAL test Redis (db 15).

Start it first with `docker compose up -d redis`.
"""

import uuid

from prometheus_client import REGISTRY

from app.config import Settings
from app.core.redis import create_redis
from tests.conftest import TEST_DATABASE_URL, TEST_REDIS_URL
from tests.integration.conftest import client_for


def errors(op: str) -> float:
    return REGISTRY.get_sample_value("redis_errors_total", {"op": op}) or 0.0


async def test_healthy_redis_returns_real_values_and_counts_no_errors() -> None:
    r = create_redis(Settings(database_url=TEST_DATABASE_URL, redis_url=TEST_REDIS_URL))
    key = f"test:{uuid.uuid4()}"
    before = errors("t_ok")

    assert await r.call("t_ok", r.client.set(key, "v", ex=30), False) is True
    assert await r.call("t_ok", r.client.get(key), "FALLBACK") == "v"
    # A missing key is a normal answer (None), not an error and not the fallback.
    assert await r.call("t_ok", r.client.get(f"{key}:missing"), "FALLBACK") is None

    assert errors("t_ok") == before
    await r.aclose()


async def test_readyz_reports_redis_ok() -> None:
    async with client_for(Settings()) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "postgres": "ok", "redis": "ok"}


async def test_app_boots_and_stays_ready_when_redis_is_down() -> None:
    settings = Settings(database_url=TEST_DATABASE_URL, redis_url="redis://127.0.0.1:1/0")
    before = errors("readyz")
    async with client_for(settings) as client:
        # Liveness and readiness both work: Redis down is "degraded", never "not ready" (D31).
        assert (await client.get("/healthz")).status_code == 200
        resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "postgres": "ok", "redis": "degraded"}
    assert errors("readyz") == before + 1


async def test_readyz_postgres_down_and_redis_down_is_503_with_both_reported() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://nobody:nopass@127.0.0.1:1/none",
        redis_url="redis://127.0.0.1:1/0",
    )
    async with client_for(settings) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable", "postgres": "down", "redis": "degraded"}
