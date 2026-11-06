"""Queueing a click on every successful GET redirect (task P3.1, ARCHITECTURE §2.1 step 6, §2.2).

Real app, real Postgres, real Redis (db 15). The worker that consumes the stream is P3.2.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import redis.asyncio as aioredis
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_now
from app.config import Settings
from app.models import User
from app.services.clicks import hash_ip
from tests.conftest import TEST_DATABASE_URL, TEST_IP_HASH_SECRET, TEST_REDIS_URL
from tests.integration.conftest import running_app
from tests.integration.factories import make_link, make_user

NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
DEST = "https://example.com/landing"
DEAD_REDIS = "redis://127.0.0.1:1/0"
VISITOR_IP = "203.0.113.7"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"


def settings_with(**overrides: object) -> Settings:
    values: dict[str, object] = {"database_url": TEST_DATABASE_URL, **overrides}
    return Settings(**values)  # type: ignore[arg-type]


def metric(name: str) -> float:
    return REGISTRY.get_sample_value(name) or 0.0


@pytest.fixture
async def owner(db: AsyncSession) -> User:
    user = await make_user(db, "owner@example.com")
    await make_link(db, user, "click01", destination_url=DEST)
    await make_link(db, user, "off0001", destination_url=DEST, is_active=False)
    await make_link(db, user, "old0001", destination_url=DEST, expires_at=NOW - timedelta(days=1))
    return user


@pytest.fixture
async def raw() -> AsyncIterator[aioredis.Redis]:
    r = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield r
    await r.aclose()


async def stream(raw: aioredis.Redis) -> list[dict[str, str]]:
    return [fields for _, fields in await raw.xrange("clicks")]


def pinned(**settings: object):  # type: ignore[no-untyped-def]
    return running_app(settings_with(**settings), overrides={get_now: lambda: NOW})


# --- what is queued ---------------------------------------------------------------------------


async def test_a_redirect_queues_exactly_one_event_with_the_documented_fields(
    owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    link_id = str((await make_link(db, owner, "fields1")).id)
    async with pinned(trusted_proxy_count=1) as (_, c):
        resp = await c.get(
            "/fields1",
            headers={
                "X-Forwarded-For": f"6.6.6.6, {VISITOR_IP}",
                "User-Agent": UA,
                "Referer": "https://news.example.com/story?id=42&token=SECRET",
            },
        )
    assert resp.status_code == 302
    (event,) = await stream(raw)
    assert set(event) == {
        "event_id",
        "link_id",
        "code",
        "ts",
        "ip_hash",
        "user_agent",
        "referrer_host",
    }
    assert uuid.UUID(event["event_id"]).version == 4
    assert event["link_id"] == link_id and event["code"] == "fields1"
    assert event["ts"] == NOW.isoformat()
    assert event["user_agent"] == UA
    assert event["referrer_host"] == "news.example.com"  # host only: no path, no query
    # The identity of the visitor is the address our trusted proxy saw, not the spoofed prefix.
    assert event["ip_hash"] == hash_ip(VISITOR_IP, TEST_IP_HASH_SECRET)


async def test_the_click_data_holds_neither_the_raw_ip_nor_the_referrer_query(
    owner: User, raw: aioredis.Redis
) -> None:
    async with pinned(trusted_proxy_count=1) as (_, c):
        await c.get(
            "/click01",
            headers={
                "X-Forwarded-For": VISITOR_IP,
                "Referer": "https://search.example/?q=my+private+query",
            },
        )
    stored = json.dumps(await stream(raw))
    assert VISITOR_IP not in stored
    assert "private" not in stored and "query" not in stored


async def test_a_missing_user_agent_and_referrer_become_empty_strings(
    owner: User, raw: aioredis.Redis
) -> None:
    async with pinned() as (_, c):
        await c.get("/click01", headers={"User-Agent": ""})
    (event,) = await stream(raw)
    assert event["user_agent"] == "" and event["referrer_host"] == ""


async def test_a_long_user_agent_is_truncated_to_512(owner: User, raw: aioredis.Redis) -> None:
    async with pinned() as (_, c):
        await c.get("/click01", headers={"User-Agent": "U" * 5000})
    assert len((await stream(raw))[0]["user_agent"]) == 512


async def test_cache_miss_and_cache_hit_both_queue_a_click(
    owner: User, raw: aioredis.Redis
) -> None:
    async with pinned() as (_, c):
        await c.get("/click01")  # miss: read from Postgres
        await c.get("/click01")  # hit: served from the Redis cache
    events = await stream(raw)
    assert len(events) == 2
    assert events[0]["event_id"] != events[1]["event_id"]  # one unique id per click
    assert events[0]["link_id"] == events[1]["link_id"]  # the cached entry carries the link id


# --- what is NOT queued -----------------------------------------------------------------------


async def test_head_never_queues_a_click(owner: User, raw: aioredis.Redis) -> None:
    before = metric("click_events_enqueued_total")
    async with pinned() as (_, c):
        await c.get("/click01")  # warm the cache so HEAD would take the fast path too
        for _ in range(3):
            assert (await c.head("/click01")).status_code == 302
    assert await raw.xlen("clicks") == 1  # only the GET
    assert metric("click_events_enqueued_total") == before + 1


@pytest.mark.parametrize(
    ("code", "status"),
    [("off0001", 404), ("old0001", 410), ("nothere1", 404), ("favicon.ico", 404)],
)
async def test_failed_lookups_queue_nothing(
    owner: User, raw: aioredis.Redis, code: str, status: int
) -> None:
    before = metric("click_events_enqueued_total")
    async with pinned() as (_, c):
        for _ in range(2):
            assert (await c.get(f"/{code}")).status_code == status
    assert await raw.exists("clicks") == 0
    assert metric("click_events_enqueued_total") == before


async def test_a_rate_limited_request_queues_nothing(owner: User, raw: aioredis.Redis) -> None:
    async with pinned(rate_limit_redirect_per_min=2) as (_, c):
        codes = [(await c.get("/click01")).status_code for _ in range(4)]
    assert codes == [302, 302, 429, 429]
    assert await raw.xlen("clicks") == 2  # the two that were served, not the two refused


async def test_the_counter_matches_the_stream(owner: User, raw: aioredis.Redis) -> None:
    before = metric("click_events_enqueued_total")
    async with pinned() as (_, c):
        for _ in range(5):
            await c.get("/click01")
    assert metric("click_events_enqueued_total") == before + 5 == before + await raw.xlen("clicks")


# --- the stream is a memory guard -------------------------------------------------------------


async def test_the_stream_is_capped_approximately(owner: User, raw: aioredis.Redis) -> None:
    """MAXLEN ~ N trims whole internal blocks (about 100 entries each), so the length stays near N
    instead of growing without bound; it is not exactly N (ARCHITECTURE §2.3.1)."""
    before = metric("click_events_enqueued_total")
    async with pinned(click_stream_maxlen=100, rate_limit_redirect_per_min=10000) as (_, c):
        for _ in range(450):
            await c.get("/click01")
    enqueued = metric("click_events_enqueued_total") - before  # events that really were added
    length = await raw.xlen("clicks")
    # Compared with what was actually added (a slow machine can drop a few clicks), not with 450.
    assert enqueued > 300
    assert 100 <= length < enqueued, (length, enqueued)


# --- Redis trouble: the redirect still works and the loss is counted --------------------------


async def test_redis_down_redirect_still_302_and_the_click_is_counted_as_dropped(
    owner: User,
) -> None:
    dropped, enqueued = metric("click_events_dropped_total"), metric("click_events_enqueued_total")
    async with pinned(redis_url=DEAD_REDIS) as (_, c):
        resp = await c.get("/click01")
    assert (resp.status_code, resp.headers["location"]) == (302, DEST)
    assert metric("click_events_dropped_total") == dropped + 1
    assert metric("click_events_enqueued_total") == enqueued


async def test_a_hung_redis_does_not_delay_the_redirect_beyond_one_bound(owner: User) -> None:
    held: list[asyncio.StreamWriter] = []

    async def silent(_: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        held.append(w)

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    dropped = metric("click_events_dropped_total")
    try:
        async with pinned(redis_url=f"redis://127.0.0.1:{port}/0") as (_, c):
            started = time.monotonic()
            resp = await c.get("/click01")
            elapsed = time.monotonic() - started
    finally:
        for w in held:
            w.close()
        server.close()
        await server.wait_closed()
    assert resp.status_code == 302
    assert elapsed < 0.6  # one failed Redis call at most (the rate limiter), never one per step
    assert metric("click_events_dropped_total") == dropped + 1


async def test_a_failing_xadd_alone_drops_the_click_but_not_the_redirect(
    owner: User, raw: aioredis.Redis
) -> None:
    """Redis is healthy, but the key `clicks` holds a plain string, so XADD really fails."""
    await raw.set("clicks", "not a stream")
    dropped = metric("click_events_dropped_total")
    errors = REGISTRY.get_sample_value("redis_errors_total", {"op": "enqueue"}) or 0.0
    async with pinned() as (_, c):
        resp = await c.get("/click01")
    assert (resp.status_code, resp.headers["location"]) == (302, DEST)
    assert metric("click_events_dropped_total") == dropped + 1
    assert REGISTRY.get_sample_value("redis_errors_total", {"op": "enqueue"}) == errors + 1
    assert await raw.get("clicks") == "not a stream"  # untouched


async def test_the_redirect_does_not_wait_for_the_enqueue(
    owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The XADD is a background task: it runs after the response has been sent, so a slow queue
    costs the visitor nothing. httpx buffers whole responses, so this talks ASGI directly and
    records when the response starts versus when the enqueue finishes."""
    from app.services import clicks

    events: list[tuple[str, float]] = []
    original = clicks.enqueue_click

    async def slow_enqueue(*args: object, **kwargs: object) -> None:
        events.append(("enqueue started", time.monotonic()))
        await asyncio.sleep(0.3)
        await original(*args, **kwargs)  # type: ignore[arg-type]
        events.append(("enqueue finished", time.monotonic()))

    monkeypatch.setattr(clicks, "enqueue_click", slow_enqueue)

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start":
            events.append((f"response status {message['status']}", time.monotonic()))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/click01",
        "raw_path": b"/click01",
        "query_string": b"",
        "headers": [(b"host", b"test")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
        "scheme": "http",
    }
    async with pinned() as (app, _):
        await app(scope, receive, send)  # returns after the background task has finished

    order = [name for name, _ in events]
    assert order == ["response status 302", "enqueue started", "enqueue finished"], order
    times = dict(events)
    assert times["enqueue finished"] - times["response status 302"] >= 0.25  # sent first
