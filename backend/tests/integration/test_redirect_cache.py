"""The redirect cache (task P2.2, ARCHITECTURE §2.1 / §2.1.1). Real Postgres AND real Redis (db 15).

Whenever a test says "directly in Postgres" it changes a row WITHOUT going through the API, which
is how it proves an answer came from the cache and not from the database.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import redis.asyncio as aioredis
from prometheus_client import REGISTRY
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Link, User
from app.services import link_cache
from tests.conftest import TEST_DATABASE_URL, TEST_REDIS_URL
from tests.integration.conftest import ApiAs, running_app
from tests.integration.factories import make_link, make_user

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
OLD = "https://example.com/old"
NEW = "https://example.com/new"
DEAD_DB = "postgresql+asyncpg://nobody:nopass@127.0.0.1:1/none"
DEAD_REDIS = "redis://127.0.0.1:1/0"  # nothing listens on port 1: refused immediately


def metric(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def cache_result(result: str) -> float:
    return metric("redirect_cache_total", result=result)


def redis_errors(op: str) -> float:
    return metric("redis_errors_total", op=op)


@pytest.fixture
async def owner(db: AsyncSession) -> User:
    return await make_user(db, "owner@example.com")


@pytest.fixture
async def raw() -> AsyncIterator[aioredis.Redis]:
    """A plain Redis connection for looking at (and tampering with) the cache directly."""
    r = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield r
    await r.aclose()


async def set_destination_in_db(db: AsyncSession, code: str, url: str) -> None:
    """Change a link in Postgres behind the cache's back (no invalidation)."""
    await db.execute(update(Link).where(Link.code == code).values(destination_url=url))
    await db.commit()


# --- miss -> hit ------------------------------------------------------------------------------


async def test_miss_fills_the_cache_then_hit_is_served_from_it(
    api_as: ApiAs, owner: User, db: AsyncSession, raw: aioredis.Redis
) -> None:
    link = await make_link(db, owner, "cache01", destination_url=OLD)
    miss, hit = cache_result("miss"), cache_result("hit")
    async with api_as(owner, now=NOW) as (_, client):
        first = await client.get("/cache01")
        assert (first.status_code, first.headers["location"]) == (302, OLD)
        assert cache_result("miss") == miss + 1

        # What was stored: the link id (for click events later) and the URL, with a bounded TTL.
        stored = json.loads(await raw.get("link:cache01") or "{}")
        assert stored == {"id": str(link.id), "url": OLD}
        assert 0 < await raw.ttl("link:cache01") <= 300

        # Change Postgres behind the cache's back: a hit must still answer with the OLD value.
        await set_destination_in_db(db, "cache01", NEW)
        second = await client.get("/cache01")
        assert (second.status_code, second.headers["location"]) == (302, OLD)
        assert cache_result("hit") == hit + 1
        assert cache_result("miss") == miss + 1  # the second request did not reach Postgres

        # HEAD uses the same cache.
        head = await client.head("/cache01")
        assert (head.status_code, head.headers["location"]) == (302, OLD)


# --- negative cache ---------------------------------------------------------------------------


async def test_unknown_code_is_negatively_cached_for_60_seconds_at_most(
    api_as: ApiAs, owner: User, raw: aioredis.Redis
) -> None:
    neg = cache_result("negative")
    async with api_as(owner, now=NOW) as (_, client):
        assert (await client.get("/ghost01")).status_code == 404
        assert await raw.get("neg:ghost01") == "1"
        assert 0 < await raw.ttl("neg:ghost01") <= 60

        assert (await client.get("/ghost01")).status_code == 404
        assert cache_result("negative") == neg + 1


async def test_disabled_link_is_negatively_cached_and_enabling_is_immediate(
    api_as: ApiAs, owner: User, db: AsyncSession, raw: aioredis.Redis
) -> None:
    link = await make_link(db, owner, "off0001", destination_url=OLD, is_active=False)
    async with api_as(owner, now=NOW) as (_, client):
        assert (await client.get("/off0001")).status_code == 404
        assert await raw.exists("neg:off0001") == 1

        patched = await client.patch(f"/api/v1/links/{link.id}", json={"is_active": True})
        assert patched.status_code == 200
        assert await raw.exists("neg:off0001") == 0  # DEL on write
        resp = await client.get("/off0001")
        assert (resp.status_code, resp.headers["location"]) == (302, OLD)


async def test_creating_a_link_clears_an_earlier_negative_entry(
    api_as: ApiAs, owner: User, raw: aioredis.Redis
) -> None:
    async with api_as(owner, now=NOW) as (_, client):
        assert (await client.get("/newone1")).status_code == 404  # now negatively cached
        created = await client.post(
            "/api/v1/links", json={"destination_url": OLD, "alias": "newone1"}
        )
        assert created.status_code == 201
        resp = await client.get("/newone1")  # would be a cached 404 if create did not DEL
        assert (resp.status_code, resp.headers["location"]) == (302, OLD)


# --- invalidation on write --------------------------------------------------------------------


async def test_edit_is_visible_on_the_very_next_redirect(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "edit001", destination_url=OLD)
    async with api_as(owner, now=NOW) as (_, client):
        assert (await client.get("/edit001")).headers["location"] == OLD  # cached
        patched = await client.patch(f"/api/v1/links/{link.id}", json={"destination_url": NEW})
        assert patched.status_code == 200
        assert (await client.get("/edit001")).headers["location"] == NEW


async def test_disabling_is_visible_on_the_very_next_redirect(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "dis0001", destination_url=OLD)
    async with api_as(owner, now=NOW) as (_, client):
        assert (await client.get("/dis0001")).status_code == 302  # cached
        await client.patch(f"/api/v1/links/{link.id}", json={"is_active": False})
        assert (await client.get("/dis0001")).status_code == 404


async def test_setting_an_expiry_in_the_past_side_is_visible_immediately(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "exp0001", destination_url=OLD)
    async with api_as(owner, now=NOW) as (_, client):
        assert (await client.get("/exp0001")).status_code == 302  # cached with no expiry
        soon = (NOW + timedelta(seconds=90)).isoformat()
        await client.patch(f"/api/v1/links/{link.id}", json={"expires_at": soon})
        # The old entry is gone; the next lookup re-caches with a TTL bounded by the new expiry.
        assert (await client.get("/exp0001")).status_code == 302


async def test_delete_is_visible_on_the_very_next_redirect(
    api_as: ApiAs, owner: User, db: AsyncSession, raw: aioredis.Redis
) -> None:
    link = await make_link(db, owner, "del0001", destination_url=OLD)
    async with api_as(owner, now=NOW) as (_, client):
        assert (await client.get("/del0001")).status_code == 302  # cached
        assert (await client.delete(f"/api/v1/links/{link.id}")).status_code == 204
        assert await raw.exists("link:del0001") == 0
        assert (await client.get("/del0001")).status_code == 404


# --- TTL rules --------------------------------------------------------------------------------


def test_ttl_is_jittered_but_never_above_the_configured_maximum() -> None:
    settings = Settings(database_url=TEST_DATABASE_URL, link_cache_ttl_seconds=300)
    ttls = {link_cache.cache_ttl_seconds(settings, None, NOW) for _ in range(500)}
    assert max(ttls) <= 300  # the invariant: never above 300 s
    assert min(ttls) >= 270  # -10% at most
    assert len(ttls) > 1  # actually jittered


async def test_ttl_never_outlives_the_link(
    api_as: ApiAs, owner: User, db: AsyncSession, raw: aioredis.Redis
) -> None:
    await make_link(
        db, owner, "short01", destination_url=OLD, expires_at=NOW + timedelta(seconds=40)
    )
    async with api_as(owner, now=NOW) as (_, client):
        assert (await client.get("/short01")).status_code == 302
        assert 0 < await raw.ttl("link:short01") <= 40


async def test_a_link_expiring_within_a_second_is_not_cached_at_all(
    api_as: ApiAs, owner: User, db: AsyncSession, raw: aioredis.Redis
) -> None:
    await make_link(
        db, owner, "tiny001", destination_url=OLD, expires_at=NOW + timedelta(milliseconds=900)
    )
    async with api_as(owner, now=NOW) as (_, client):
        assert (await client.get("/tiny001")).status_code == 302
        assert await raw.exists("link:tiny001") == 0


async def test_expired_links_are_410_and_never_cached(
    api_as: ApiAs, owner: User, db: AsyncSession, raw: aioredis.Redis
) -> None:
    await make_link(db, owner, "old0001", expires_at=NOW - timedelta(seconds=1))
    async with api_as(owner, now=NOW) as (_, client):
        assert (await client.get("/old0001")).status_code == 410
        assert (await client.get("/old0001")).status_code == 410
        assert await raw.exists("link:old0001", "neg:old0001") == 0


# --- the documented stale race (ARCHITECTURE §2.1.1) ------------------------------------------


async def test_stale_race_entry_is_served_but_expires_within_the_ttl(
    api_as: ApiAs, owner: User, db: AsyncSession, raw: aioredis.Redis
) -> None:
    """Reader A fetched the OLD row; writer B updated and deleted the key; THEN A writes the old
    value back. Delete-on-write cannot prevent that, so the design bounds it by the TTL."""
    link = await make_link(db, owner, "race001", destination_url=OLD)
    settings = Settings(database_url=TEST_DATABASE_URL, link_cache_ttl_seconds=1)
    async with api_as(owner, now=NOW, settings=settings) as (app, client):
        # B: edit (commit + DEL).
        await client.patch(f"/api/v1/links/{link.id}", json={"destination_url": NEW})
        assert await raw.exists("link:race001") == 0
        # A: its late write of the OLD value, through the real cache-write function.
        await link_cache.cache_link(app.state.redis, settings, "race001", link.id, OLD, None, NOW)

        # Accepted limitation: the stale value is served...
        assert (await client.get("/race001")).headers["location"] == OLD
        # ...but only for the (here 1 s) TTL, never longer.
        assert (
            0 < await raw.pttl("link:race001") <= 1000
        )  # milliseconds: TTL (seconds) rounds to 0 when slow
        await asyncio.sleep(1.3)
        assert (await client.get("/race001")).headers["location"] == NEW


# --- Redis trouble: always a normal answer ----------------------------------------------------


async def test_redis_down_redirect_still_302_and_pays_for_one_failed_call_only(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    await make_link(db, owner, "nore001", destination_url=OLD)
    settings = Settings(database_url=TEST_DATABASE_URL, redis_url=DEAD_REDIS)
    ops = ("ratelimit", "cache_get", "neg_get", "cache_set")
    before = {op: redis_errors(op) for op in ops}
    async with api_as(owner, now=NOW, settings=settings) as (_, client):
        resp = await client.get("/nore001")
    assert (resp.status_code, resp.headers["location"]) == (302, OLD)
    # The rate limiter is the first Redis call and it failed (open); every later Redis step of the
    # same request is skipped, not retried.
    assert redis_errors("ratelimit") == before["ratelimit"] + 1
    assert redis_errors("cache_get") == before["cache_get"]
    assert redis_errors("neg_get") == before["neg_get"]
    assert redis_errors("cache_set") == before["cache_set"]


async def test_redis_down_unknown_404_and_expired_410_are_unchanged(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    await make_link(db, owner, "old0001", expires_at=NOW - timedelta(seconds=1))
    await make_link(db, owner, "off0001", is_active=False)
    settings = Settings(database_url=TEST_DATABASE_URL, redis_url=DEAD_REDIS)
    async with api_as(owner, now=NOW, settings=settings) as (_, client):
        assert (await client.get("/nope001")).status_code == 404
        assert (await client.get("/off0001")).status_code == 404
        assert (await client.get("/old0001")).status_code == 410


async def test_hung_redis_costs_one_timeout_not_a_hung_request(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    await make_link(db, owner, "hung001", destination_url=OLD)

    held: list[asyncio.StreamWriter] = []

    async def accept_and_stay_silent(_: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        held.append(w)

    server = await asyncio.start_server(accept_and_stay_silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    settings = Settings(database_url=TEST_DATABASE_URL, redis_url=f"redis://127.0.0.1:{port}/0")
    try:
        async with api_as(owner, now=NOW, settings=settings) as (_, client):
            started = time.monotonic()
            resp = await client.get("/hung001")
            elapsed = time.monotonic() - started
    finally:
        for w in held:
            w.close()
        server.close()
        await server.wait_closed()
    assert (resp.status_code, resp.headers["location"]) == (302, OLD)
    # 100 ms socket timeout for the one failed call, plus the database; three calls would be 300+.
    assert elapsed < 0.6


async def test_edits_succeed_when_redis_is_down_and_count_the_failed_delete(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    """Editing or deleting works with Redis down; the cache DEL that fails is counted, and the old
    cached value (if any) lives until its TTL. (CREATING a link is different: its rate limiter
    fails CLOSED, decision D13, see test_rate_limit.)"""
    link = await make_link(db, owner, "wr00001", destination_url=OLD)
    settings = Settings(database_url=TEST_DATABASE_URL, redis_url=DEAD_REDIS)
    before = redis_errors("cache_del")
    async with api_as(owner, now=NOW, settings=settings) as (_, client):
        patched = await client.patch(f"/api/v1/links/{link.id}", json={"destination_url": NEW})
        deleted = await client.delete(f"/api/v1/links/{link.id}")
    assert (patched.status_code, deleted.status_code) == (200, 204)
    assert redis_errors("cache_del") == before + 2


async def test_postgres_down_on_a_cache_hit_still_redirects_and_on_a_miss_is_503(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    await make_link(db, owner, "hit0001", destination_url=OLD)
    async with api_as(owner, now=NOW) as (_, client):
        assert (await client.get("/hit0001")).status_code == 302  # fills the cache

    # A second app instance whose Postgres is dead but which shares the (real) Redis.
    async with running_app(Settings(database_url=DEAD_DB)) as (_, dead_db_client):
        hit = await dead_db_client.get("/hit0001")
        hit_head = await dead_db_client.head("/hit0001")
        miss = await dead_db_client.get("/other01")
    assert (hit.status_code, hit.headers["location"]) == (302, OLD)  # accepted trade-off
    assert hit_head.status_code == 302
    assert miss.status_code == 503  # a miss needs Postgres, and it is down


async def test_unreadable_cache_entry_is_treated_as_a_miss_and_repaired(
    api_as: ApiAs, owner: User, db: AsyncSession, raw: aioredis.Redis
) -> None:
    link = await make_link(db, owner, "bad0001", destination_url=OLD)
    await raw.set("link:bad0001", "this is not json", ex=60)
    async with api_as(owner, now=NOW) as (_, client):
        resp = await client.get("/bad0001")
    assert (resp.status_code, resp.headers["location"]) == (302, OLD)
    assert json.loads(await raw.get("link:bad0001") or "{}") == {"id": str(link.id), "url": OLD}


async def test_junk_codes_never_reach_the_cache_or_postgres(
    api_as: ApiAs, owner: User, raw: aioredis.Redis
) -> None:
    async with api_as(owner, now=NOW) as (_, client):
        assert (await client.get("/favicon.ico")).status_code == 404
        assert (await client.get("/a%00b")).status_code == 404
    # Only the rate-limit counter (junk traffic counts against the limit, on purpose): nothing
    # was cached for the junk codes.
    assert await raw.keys("link:*") == [] and await raw.keys("neg:*") == []
