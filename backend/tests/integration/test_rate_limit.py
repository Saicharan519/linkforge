"""Rate limiting (task P2.3, ARCHITECTURE §2.1 step 1, §5, §6.3). Real Redis (db 15) + Postgres."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import redis.asyncio as aioredis
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_now
from app.config import Settings
from app.core.errors import ApiError
from app.core.rate_limit import RateLimiter
from app.core.redis import create_redis
from app.models import User
from tests.conftest import TEST_DATABASE_URL, TEST_REDIS_URL
from tests.integration.conftest import running_app
from tests.integration.factories import make_link, make_user

NOW = datetime(2026, 11, 5, 12, 0, 30, tzinfo=UTC)  # 30 s into a minute: Retry-After is 30
DEAD_REDIS = "redis://127.0.0.1:1/0"


def metric(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def settings_with(**overrides: object) -> Settings:
    return Settings(database_url=TEST_DATABASE_URL, **overrides)  # type: ignore[arg-type]


@pytest.fixture
async def owner(db: AsyncSession) -> User:
    user = await make_user(db, "owner@example.com")
    await make_link(db, user, "rate001", destination_url="https://example.com/x")
    return user


@pytest.fixture
async def raw() -> AsyncIterator[aioredis.Redis]:
    r = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def limiter() -> AsyncIterator[RateLimiter]:
    redis = create_redis(settings_with(redis_url=TEST_REDIS_URL))
    yield RateLimiter(redis)
    await redis.aclose()


# --- the limiter itself -----------------------------------------------------------------------


async def test_limit_then_429_with_retry_after(limiter: RateLimiter) -> None:
    kw = dict(limit=3, window_seconds=60, now_epoch=NOW.timestamp(), fail_open=True)
    for _ in range(3):
        assert await limiter.enforce("redirect", "1.2.3.4", **kw) is True  # type: ignore[arg-type]
    with pytest.raises(ApiError) as exc:
        await limiter.enforce("redirect", "1.2.3.4", **kw)  # type: ignore[arg-type]
    assert exc.value.status_code == 429
    assert exc.value.code == "rate_limited"
    assert exc.value.headers == {"Retry-After": "30"}  # the window ends 30 s from NOW


async def test_counters_are_per_identifier_scope_and_window(limiter: RateLimiter) -> None:
    kw = dict(limit=1, window_seconds=60, fail_open=True)
    t = NOW.timestamp()
    await limiter.enforce("redirect", "A", now_epoch=t, **kw)  # type: ignore[arg-type]
    # Another identifier, another scope, and the next minute each have their own counter.
    await limiter.enforce("redirect", "B", now_epoch=t, **kw)  # type: ignore[arg-type]
    await limiter.enforce("create", "A", now_epoch=t, **kw)  # type: ignore[arg-type]
    await limiter.enforce("redirect", "A", now_epoch=t + 60, **kw)  # type: ignore[arg-type]
    with pytest.raises(ApiError):
        await limiter.enforce("redirect", "A", now_epoch=t, **kw)  # type: ignore[arg-type]


async def test_concurrent_requests_are_counted_exactly(limiter: RateLimiter) -> None:
    """50 parallel callers, limit 10: exactly 10 get through (INCR is atomic, no lost updates)."""

    async def one() -> bool:
        try:
            return await limiter.enforce(
                "redirect",
                "burst",
                limit=10,
                window_seconds=60,
                now_epoch=NOW.timestamp(),
                fail_open=True,
            )
        except ApiError:
            return False

    results = await asyncio.gather(*(one() for _ in range(50)))
    assert sum(results) == 10


async def test_counter_always_has_an_expiry(limiter: RateLimiter, raw: aioredis.Redis) -> None:
    kw = dict(limit=5, window_seconds=60, now_epoch=NOW.timestamp(), fail_open=True)
    await limiter.enforce("redirect", "ttl", **kw)  # type: ignore[arg-type]
    key = "rl:redirect:ttl:" + str(int(NOW.timestamp() // 60))
    assert 0 < await raw.ttl(key) <= 60

    # A counter that somehow lost its expiry is repaired by the next request (it would otherwise
    # block that caller for as long as Redis lives).
    await raw.persist(key)
    assert await raw.ttl(key) == -1
    await limiter.enforce("redirect", "ttl", **kw)  # type: ignore[arg-type]
    assert 0 < await raw.ttl(key) <= 60


async def test_rejections_are_counted_per_scope(limiter: RateLimiter) -> None:
    before = metric("rate_limit_rejections_total", scope="login")
    kw = dict(limit=1, window_seconds=900, now_epoch=NOW.timestamp(), fail_open=False)
    await limiter.enforce("login", "ip|email", **kw)  # type: ignore[arg-type]
    with pytest.raises(ApiError):
        await limiter.enforce("login", "ip|email", **kw)  # type: ignore[arg-type]
    assert metric("rate_limit_rejections_total", scope="login") == before + 1


# --- Redis down: the two policies -------------------------------------------------------------


async def test_redis_down_fail_open_lets_the_request_through() -> None:
    redis = create_redis(settings_with(redis_url=DEAD_REDIS))
    before = metric("redis_errors_total", op="ratelimit")
    try:
        answered = await RateLimiter(redis).enforce(
            "redirect", "x", limit=1, window_seconds=60, now_epoch=0, fail_open=True
        )
    finally:
        await redis.aclose()
    assert answered is False  # allowed, and the caller learns Redis did not answer
    assert metric("redis_errors_total", op="ratelimit") == before + 1


@pytest.mark.parametrize("scope", ["login", "create"])
async def test_redis_down_fail_closed_denies_the_request(scope: str) -> None:
    redis = create_redis(settings_with(redis_url=DEAD_REDIS))
    try:
        with pytest.raises(ApiError) as exc:
            await RateLimiter(redis).enforce(
                scope, "x", limit=5, window_seconds=900, now_epoch=0, fail_open=False
            )
    finally:
        await redis.aclose()
    # 503, not 429: nobody exceeded a limit; we just cannot count, so we cannot allow it.
    assert (exc.value.status_code, exc.value.code) == (503, "unavailable")


# --- the redirect path ------------------------------------------------------------------------


async def test_redirect_429_after_the_limit_get_and_head(owner: User) -> None:
    settings = settings_with(rate_limit_redirect_per_min=3)
    async with running_app(settings, overrides={get_now: lambda: NOW}) as (_, client):
        statuses = [(await client.get("/rate001")).status_code for _ in range(3)]
        assert statuses == [302, 302, 302]
        over = await client.get("/rate001")
        over_head = await client.head("/rate001")
    assert over.status_code == 429
    assert over.json()["error"]["code"] == "rate_limited"
    assert over.headers["retry-after"] == "30"
    # HEAD costs the same lookups, so it is limited too, and keeps the header but has no body.
    assert over_head.status_code == 429
    assert over_head.headers["retry-after"] == "30"
    assert over_head.content == b""


async def test_junk_and_unknown_codes_count_against_the_limit(owner: User) -> None:
    settings = settings_with(rate_limit_redirect_per_min=2)
    async with running_app(settings, overrides={get_now: lambda: NOW}) as (_, client):
        assert (await client.get("/favicon.ico")).status_code == 404
        assert (await client.get("/nope001")).status_code == 404
        assert (await client.get("/nope002")).status_code == 429  # a scanner is throttled


async def test_the_limit_resets_in_the_next_minute(owner: User) -> None:
    clock = {"now": NOW}
    settings = settings_with(rate_limit_redirect_per_min=1)
    async with running_app(settings, overrides={get_now: lambda: clock["now"]}) as (_, client):
        assert (await client.get("/rate001")).status_code == 302
        assert (await client.get("/rate001")).status_code == 429
        clock["now"] = NOW + timedelta(seconds=31)  # into the next minute
        assert (await client.get("/rate001")).status_code == 302


async def test_spoofed_x_forwarded_for_is_ignored_without_a_trusted_proxy(owner: User) -> None:
    """The caller picks a new fake address every time; with TRUSTED_PROXY_COUNT=0 it changes
    nothing: all requests share the real peer's bucket and the 3rd is rejected."""
    settings = settings_with(rate_limit_redirect_per_min=2, trusted_proxy_count=0)
    async with running_app(settings, overrides={get_now: lambda: NOW}) as (_, client):
        codes = [
            (await client.get("/rate001", headers={"X-Forwarded-For": f"9.9.9.{i}"})).status_code
            for i in range(3)
        ]
    assert codes == [302, 302, 429]


async def test_with_one_trusted_proxy_the_appended_address_is_the_identity(owner: User) -> None:
    settings = settings_with(rate_limit_redirect_per_min=2, trusted_proxy_count=1)
    async with running_app(settings, overrides={get_now: lambda: NOW}) as (_, client):

        def via_proxy(claimed: str, real: str) -> dict[str, str]:
            return {"X-Forwarded-For": f"{claimed}, {real}"}

        a1 = await client.get("/rate001", headers=via_proxy("6.6.6.1", "203.0.113.1"))
        a2 = await client.get("/rate001", headers=via_proxy("6.6.6.2", "203.0.113.1"))
        # Same real client, different spoofed prefixes: still ONE bucket, so this is the 3rd.
        a3 = await client.get("/rate001", headers=via_proxy("6.6.6.3", "203.0.113.1"))
        # A different real client has its own bucket.
        b1 = await client.get("/rate001", headers=via_proxy("6.6.6.1", "203.0.113.2"))
    assert [r.status_code for r in (a1, a2, a3, b1)] == [302, 302, 429, 302]


async def test_redis_down_redirects_are_never_limited_and_never_fail(owner: User) -> None:
    settings = settings_with(redis_url=DEAD_REDIS, rate_limit_redirect_per_min=1)
    async with running_app(settings, overrides={get_now: lambda: NOW}) as (_, client):
        statuses = [(await client.get("/rate001")).status_code for _ in range(5)]
        head = await client.head("/rate001")
    assert statuses == [302] * 5  # fail OPEN: well over the limit of 1, still served
    assert head.status_code == 302


def test_limit_settings_default_to_the_architecture_values() -> None:
    s = settings_with()
    assert (
        s.rate_limit_redirect_per_min,
        s.rate_limit_create_per_min,
        s.rate_limit_login_max,
        s.trusted_proxy_count,
    ) == (120, 30, 5, 0)
