"""Request ids, HTTP metrics, the access log, the metrics server and the click-stream gauges
(task P3.6, ARCHITECTURE §7). Real Postgres and Redis; the end-to-end test also runs a real worker.
"""

import asyncio
import io
import json
import math
import os
import signal
import socket
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime

import httpx
import pytest
import redis.asyncio as aioredis
from fastapi import HTTPException
from prometheus_client import REGISTRY
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_now
from app.config import Settings
from app.core.logging import configure_logging
from app.core.metrics import (
    CLICK_STREAM_LAG,
    CLICK_STREAM_LENGTH,
    CLICK_STREAM_PENDING,
)
from app.core.redis import SafeRedis, create_redis
from app.core.stream_metrics import GROUP as GAUGE_GROUP
from app.core.stream_metrics import refresh_stream_gauges, run_gauge_refresher
from app.db import create_engine, create_sessionmaker
from app.main import create_app
from app.models import User
from app.services.clicks import STREAM
from app.worker.consumer import GROUP, ClickConsumer, create_worker_redis
from tests.conftest import TEST_DATABASE_URL, TEST_IP_HASH_SECRET, TEST_REDIS_URL
from tests.integration.browser import ORIGIN, PASSWORD_HASH, Browser
from tests.integration.conftest import BACKEND_DIR, running_app
from tests.integration.factories import make_link, make_user
from tests.integration.test_worker_batch import HUMAN_UA, add, events

DEAD_REDIS = "redis://127.0.0.1:1/0"

# Every metric name of ARCHITECTURE §7 (plus the rate-limit counter of §7's list).
ALL_NAMES = [
    "http_request_duration_seconds",
    "http_requests_total",
    "redirect_cache_total",
    "redis_errors_total",
    "click_events_enqueued_total",
    "click_events_dropped_total",
    "click_events_processed_total",
    "click_stream_lag",
    "click_stream_pending",
    "click_stream_length",
    "click_worker_batch_seconds",
    "rate_limit_rejections_total",
]


def settings_with(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": TEST_DATABASE_URL,
        "redis_url": TEST_REDIS_URL,
        "allowed_origins": ORIGIN,
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def parse_scrape(text: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    out: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
    for family in text_string_to_metric_families(text):
        for s in family.samples:
            out[(s.name, tuple(sorted(s.labels.items())))] = s.value
    return out


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def scrape(port: int) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"http://127.0.0.1:{port}/metrics")
    assert resp.status_code == 200
    return resp.text


@pytest.fixture
async def owner(db: AsyncSession) -> User:
    user = await make_user(db, "owner@example.com", password_hash=PASSWORD_HASH)
    await make_link(db, user, "obs0001", destination_url="https://example.com/obs")
    return user


@pytest.fixture
async def raw() -> AsyncIterator[aioredis.Redis]:
    r = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def log_lines() -> Callable[[], list[dict[str, object]]]:
    """Capture the JSON log output of the app under test."""
    buffer = io.StringIO()
    configure_logging("INFO", stream=buffer)
    return lambda: [json.loads(ln) for ln in buffer.getvalue().splitlines() if ln.startswith("{")]


def access_lines(lines: list[dict[str, object]]) -> list[dict[str, object]]:
    return [ln for ln in lines if ln["logger"] == "app.access"]


# --- request id -------------------------------------------------------------------------------


async def test_every_response_carries_a_fresh_request_id(owner: User) -> None:
    async with running_app(settings_with()) as (_, c):
        ids = {(await c.get("/obs0001")).headers["x-request-id"] for _ in range(3)}
    assert len(ids) == 3 and all(len(i) == 32 for i in ids)


async def test_a_sane_incoming_request_id_is_echoed_and_a_bad_one_replaced(owner: User) -> None:
    async with running_app(settings_with()) as (_, c):
        kept = await c.get("/obs0001", headers={"X-Request-ID": "trace-abc.123"})
        bad = await c.get("/obs0001", headers={"X-Request-ID": "<script>alert(1)</script>"})
        too_long = await c.get("/obs0001", headers={"X-Request-ID": "x" * 200})
    assert kept.headers["x-request-id"] == "trace-abc.123"
    assert bad.headers["x-request-id"] != "<script>alert(1)</script>"
    assert len(too_long.headers["x-request-id"]) == 32


async def test_error_responses_and_host_guard_answers_carry_the_id_too(owner: User) -> None:
    async with running_app(settings_with(public_redirect_host="go.example.com")) as (_, c):
        not_found = await c.get("/nothere1")
        guarded = await c.get("/api/v1/links", headers={"Host": "go.example.com"})
        unauthorised = await c.get("/api/v1/links")
    assert guarded.status_code == 404 and unauthorised.status_code == 401
    for resp in (not_found, guarded, unauthorised):
        assert len(resp.headers["x-request-id"]) == 32


# --- the access log ---------------------------------------------------------------------------


async def test_one_json_access_line_per_request_with_the_documented_fields(
    owner: User, log_lines: Callable[[], list[dict[str, object]]]
) -> None:
    async with running_app(settings_with()) as (_, c):
        configure_logging("INFO", stream=io.StringIO())  # placeholder; real capture below
    buffer = io.StringIO()
    async with running_app(settings_with()) as (_, c):
        configure_logging(
            "INFO", stream=buffer
        )  # after create_app, which configures logging itself
        resp = await c.get("/obs0001", headers={"X-Request-ID": "trace-1"})
        await c.get("/api/v1/links")
    lines = [json.loads(ln) for ln in buffer.getvalue().splitlines() if ln.startswith("{")]
    access = access_lines(lines)
    assert len(access) == 2
    first = access[0]
    assert first["msg"] == "request" and first["level"] == "INFO"
    assert first["request_id"] == "trace-1" == resp.headers["x-request-id"]
    assert (first["method"], first["path"], first["route"], first["status"]) == (
        "GET",
        "/obs0001",
        "/{code}",
        302,
    )
    assert isinstance(first["duration_ms"], float) and first["duration_ms"] >= 0
    assert datetime.fromisoformat(str(first["ts"])).tzinfo is not None
    assert access[1]["route"] == "/api/v1/links" and access[1]["status"] == 401


async def test_every_log_line_of_a_request_shares_its_request_id(owner: User) -> None:
    buffer = io.StringIO()
    async with running_app(settings_with(redis_url=DEAD_REDIS)) as (_, c):
        configure_logging("INFO", stream=buffer)
        resp = await c.get("/obs0001")  # the limiter logs a Redis warning while handling it
    rid = resp.headers["x-request-id"]
    lines = [json.loads(ln) for ln in buffer.getvalue().splitlines() if ln.startswith("{")]
    warnings = [ln for ln in lines if ln["level"] == "WARNING"]
    assert warnings and all(ln["request_id"] == rid for ln in warnings)
    assert any(ln["logger"] == "app.access" and ln["request_id"] == rid for ln in lines)


async def test_healthy_probes_are_not_logged_but_failing_ones_are(owner: User) -> None:
    buffer = io.StringIO()
    async with running_app(settings_with()) as (_, c):
        configure_logging("INFO", stream=buffer)
        await c.get("/healthz")
        await c.get("/readyz")
    assert (
        access_lines(
            [json.loads(ln) for ln in buffer.getvalue().splitlines() if ln.startswith("{")]
        )
        == []
    )

    buffer = io.StringIO()
    dead = settings_with(database_url="postgresql+asyncpg://nobody:nopass@127.0.0.1:1/none")
    async with running_app(dead) as (_, c):
        configure_logging("INFO", stream=buffer)
        assert (await c.get("/readyz")).status_code == 503
    failed = access_lines(
        [json.loads(ln) for ln in buffer.getvalue().splitlines() if ln.startswith("{")]
    )
    assert len(failed) == 1 and failed[0]["status"] == 503


async def test_no_secret_and_no_client_address_ever_reaches_the_logs(owner: User) -> None:
    """Passwords, session ids, CSRF tokens, API keys and raw IPs must not appear in any log line,
    across a whole session: register, login, create a key, use it, change the password, click."""
    secret_password = "a-very-secret-password-123"
    new_password = "another-very-secret-password-456"
    real_ip = "203.0.113.77"
    buffer = io.StringIO()
    settings = settings_with(trusted_proxy_count=1)
    async with running_app(settings) as (_, c):
        configure_logging("DEBUG", stream=buffer)
        await c.post(
            "/api/v1/auth/register",
            json={"email": "leak@example.com", "password": secret_password},
            headers={"X-Forwarded-For": real_ip},
        )
        await c.post(
            "/api/v1/auth/login",
            json={"email": "leak@example.com", "password": "wrong-password-not-leaked"},
            headers={"X-Forwarded-For": real_ip},
        )
        browser = await Browser(c).login("leak@example.com", secret_password)
        session_id, csrf = c.cookies["lf_session"], c.cookies["lf_csrf"]
        key = (await browser.post("/api/v1/api-keys", json={"name": "k"})).json()["key"]
        await c.get("/api/v1/links", headers={"X-API-Key": key})
        await c.get("/api/v1/links", headers={"X-API-Key": "lf_AAAAAAAA_" + "b" * 43})
        await browser.post(
            "/api/v1/auth/change-password",
            json={"old_password": secret_password, "new_password": new_password},
        )
        await c.get(
            "/obs0001",
            headers={"X-Forwarded-For": real_ip, "Referer": "https://r.example/?token=SECRETQ"},
        )
    output = buffer.getvalue()
    assert output, "nothing was logged at all: the check below would prove nothing"
    for secret in (
        secret_password,
        new_password,
        "wrong-password-not-leaked",
        session_id,
        csrf,
        key,
        key.split("_", 2)[2],
        real_ip,
        "127.0.0.1",
        "SECRETQ",
    ):
        assert secret not in output, f"leaked into the logs: {secret[:12]}..."


# --- HTTP metrics -----------------------------------------------------------------------------


async def test_requests_are_counted_by_route_template_not_by_raw_path(owner: User) -> None:
    before = sample("http_requests_total", method="GET", route="/{code}", status="404")
    link_before = sample(
        "http_requests_total", method="GET", route="/api/v1/links/{id}", status="401"
    )
    async with running_app(settings_with()) as (_, c):
        for code in ("nocode1", "nocode2", "nocode3"):
            assert (await c.get(f"/{code}")).status_code == 404
        for _ in range(2):
            await c.get(f"/api/v1/links/{uuid.uuid4()}")
    assert sample("http_requests_total", method="GET", route="/{code}", status="404") == before + 3
    after = sample("http_requests_total", method="GET", route="/api/v1/links/{id}", status="401")
    assert after == link_before + 2
    # No series was created for a raw code or a raw id:
    labels = {
        s.labels["route"] for f in REGISTRY.collect() for s in f.samples if "route" in s.labels
    }
    assert not any("nocode" in r or len(r) > 60 for r in labels)


async def test_junk_urls_are_one_time_series_not_thousands(owner: User) -> None:
    before = sample("http_requests_total", method="GET", route="unmatched", status="404")
    async with running_app(settings_with()) as (_, c):
        for i in range(40):
            assert (await c.get(f"/scanner/probe/{i}/{uuid.uuid4().hex}")).status_code == 404
    assert (
        sample("http_requests_total", method="GET", route="unmatched", status="404") == before + 40
    )
    unmatched_series = {
        tuple(sorted(s.labels.items()))
        for f in REGISTRY.collect()
        for s in f.samples
        if s.name == "http_requests_total" and s.labels.get("route") == "unmatched"
    }
    assert all(dict(s)["route"] == "unmatched" for s in unmatched_series)


async def test_method_and_status_are_labels_including_head_and_429(owner: User) -> None:
    head = sample("http_requests_total", method="HEAD", route="/{code}", status="302")
    limited = sample("http_requests_total", method="GET", route="/{code}", status="429")
    redirects = sample("http_requests_total", method="GET", route="/{code}", status="302")
    async with running_app(
        settings_with(rate_limit_redirect_per_min=2),
        overrides={get_now: lambda: datetime(2026, 11, 6, 12, tzinfo=UTC)},
    ) as (_, c):
        await c.head("/obs0001")  # counts as one of the two allowed
        await c.get("/obs0001")
        assert (await c.get("/obs0001")).status_code == 429
    assert sample("http_requests_total", method="HEAD", route="/{code}", status="302") == head + 1
    assert (
        sample("http_requests_total", method="GET", route="/{code}", status="302") == redirects + 1
    )
    assert sample("http_requests_total", method="GET", route="/{code}", status="429") == limited + 1


async def test_the_duration_histogram_has_the_documented_buckets_and_records_time(
    owner: User,
) -> None:
    count_before = sample(
        "http_request_duration_seconds_count", method="GET", route="/{code}", status="302"
    )
    sum_before = sample(
        "http_request_duration_seconds_sum", method="GET", route="/{code}", status="302"
    )
    async with running_app(settings_with()) as (_, c):
        for _ in range(5):
            await c.get("/obs0001")
    labels = {"method": "GET", "route": "/{code}", "status": "302"}
    assert sample("http_request_duration_seconds_count", **labels) == count_before + 5
    assert sample("http_request_duration_seconds_sum", **labels) > sum_before  # real time passed
    bounds = sorted(
        float(s.labels["le"])
        for f in REGISTRY.collect()
        if f.name == "http_request_duration_seconds"
        for s in f.samples
        if s.name.endswith("_bucket")
        and s.labels.get("method") == "GET"
        and s.labels.get("route") == "/{code}"
        and s.labels.get("status") == "302"
    )
    assert bounds == [0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, math.inf]


async def test_an_unhandled_error_is_counted_as_500_and_logged(owner: User) -> None:
    async def boom() -> None:
        raise RuntimeError("deliberate failure")

    buffer = io.StringIO()
    app = create_app(settings_with())
    app.add_api_route("/api/boom", boom)
    before = sample("http_requests_total", method="GET", route="/api/boom", status="500")
    async with app.router.lifespan_context(app):
        configure_logging("INFO", stream=buffer)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/boom")
    assert resp.status_code == 500
    assert (
        sample("http_requests_total", method="GET", route="/api/boom", status="500") == before + 1
    )
    lines = [json.loads(ln) for ln in buffer.getvalue().splitlines() if ln.startswith("{")]
    assert any(ln["logger"] == "app.access" and ln["status"] == 500 for ln in lines)
    assert any("deliberate failure" in str(ln.get("exc", "")) for ln in lines)  # the traceback


async def test_http_exceptions_keep_their_status_label(owner: User) -> None:
    async def teapot() -> None:
        raise HTTPException(status_code=418, detail="short and stout")

    app = create_app(settings_with())
    app.add_api_route("/api/teapot", teapot)
    before = sample("http_requests_total", method="GET", route="/api/teapot", status="418")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            assert (await c.get("/api/teapot")).status_code == 418
    assert (
        sample("http_requests_total", method="GET", route="/api/teapot", status="418") == before + 1
    )


# --- the metrics server -----------------------------------------------------------------------


async def test_the_metrics_server_lists_every_metric_name(owner: User) -> None:
    async with running_app(settings_with(metrics_port=0)) as (app, c):
        port = app.state.metrics_port
        assert isinstance(port, int) and port > 0
        await c.get("/obs0001")
        text = await scrape(port)
    for name in ALL_NAMES:
        assert f"# HELP {name} " in text or f"# TYPE {name} " in text, f"missing: {name}"
    assert 'http_requests_total{method="GET",route="/{code}",status="302"}' in text


async def test_the_metrics_server_is_on_its_own_port_and_stops_with_the_app(owner: User) -> None:
    async with running_app(settings_with(metrics_port=0)) as (app, c):
        port = app.state.metrics_port
        public_port_serves_metrics = await c.get("/metrics")
        assert (await scrape(port)).startswith("#")
    assert public_port_serves_metrics.status_code == 404  # the app never serves /metrics itself
    with pytest.raises(httpx.ConnectError):
        await scrape(port)  # closed with the application


async def test_a_negative_metrics_port_means_no_server_at_all(owner: User) -> None:
    async with running_app(settings_with(metrics_port=-1)) as (app, _):
        assert app.state.metrics_port is None


async def test_metrics_are_never_reachable_through_the_public_short_link_host(owner: User) -> None:
    async with running_app(settings_with(public_redirect_host="go.example.com")) as (_, c):
        resp = await c.get("/metrics", headers={"Host": "go.example.com"})
    assert resp.status_code == 404


# --- the click-stream gauges ------------------------------------------------------------------


def gauges() -> tuple[float, float, float]:
    return (
        CLICK_STREAM_LENGTH._value.get(),  # type: ignore[attr-defined]
        CLICK_STREAM_LAG._value.get(),  # type: ignore[attr-defined]
        CLICK_STREAM_PENDING._value.get(),  # type: ignore[attr-defined]
    )


@pytest.fixture
async def safe_redis() -> AsyncIterator[SafeRedis]:
    redis = create_redis(settings_with())
    yield redis
    await redis.aclose()


def test_the_gauge_module_uses_the_workers_group_name() -> None:
    assert GAUGE_GROUP == GROUP  # they would silently disagree otherwise


async def test_gauges_are_zero_when_there_is_no_stream(safe_redis: SafeRedis) -> None:
    await refresh_stream_gauges(safe_redis)
    assert gauges() == (0, 0, 0)


async def test_everything_is_lag_before_any_worker_has_ever_run(
    safe_redis: SafeRedis, raw: aioredis.Redis, owner: User, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "lag0001")
    for _ in range(5):
        await add(raw, link)
    await refresh_stream_gauges(safe_redis)
    assert gauges() == (5, 5, 0)  # no group yet: all five wait


async def test_lag_pending_and_length_follow_a_worker_through_a_batch(
    safe_redis: SafeRedis,
    raw: aioredis.Redis,
    owner: User,
    db: AsyncSession,
) -> None:
    link = await make_link(db, owner, "flow001")
    engine = create_engine(settings_with(), pool_size=2, max_overflow=1)
    settings = settings_with(worker_block_ms=50, worker_batch_size=3)
    redis = create_worker_redis(settings)
    consumer = ClickConsumer(redis, create_sessionmaker(engine), settings, consumer_name="g-1")
    try:
        await consumer.ensure_group()
        for _ in range(5):
            await add(raw, link)
        await refresh_stream_gauges(safe_redis)
        assert gauges() == (5, 5, 0)  # 5 queued, nothing delivered

        entries = await consumer.read_batch()  # delivered (3) but not yet acknowledged
        assert len(entries) == 3
        await refresh_stream_gauges(safe_redis)
        assert gauges() == (5, 2, 3)  # 2 still waiting, 3 handed out and unfinished

        await consumer.handle(entries)  # stored and acknowledged
        await consumer.run_once()  # the remaining 2
        await refresh_stream_gauges(safe_redis)
        assert gauges() == (5, 0, 0)  # drained; acknowledged entries stay in the stream
    finally:
        await redis.aclose()
        await engine.dispose()


async def test_an_unknown_lag_falls_back_to_the_stream_length() -> None:
    """Redis reports `lag: nil` in some situations after trims/deletes (ARCHITECTURE §7). The gauge
    then uses XLEN, an upper bound, instead of showing nothing."""

    class StubClient:
        async def exists(self, key: str) -> int:
            return 1

        async def xlen(self, key: str) -> int:
            return 42

        async def xinfo_groups(self, key: str) -> list[dict[str, object]]:
            return [{"name": GROUP, "lag": None, "pending": 7}]

    stub = SafeRedis(StubClient(), 1.0)  # type: ignore[arg-type]
    await refresh_stream_gauges(stub)
    assert gauges() == (42, 42, 7)


async def test_gauges_become_nan_when_redis_cannot_answer_and_nothing_crashes() -> None:
    dead = create_redis(settings_with(redis_url=DEAD_REDIS))
    try:
        await refresh_stream_gauges(dead)
    finally:
        await dead.aclose()
    assert all(math.isnan(v) for v in gauges())  # "unknown", not a stale or false number


async def test_the_refresher_keeps_running_after_a_failed_round(
    safe_redis: SafeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import stream_metrics

    calls = 0
    real = stream_metrics.refresh_stream_gauges

    async def flaky(redis: SafeRedis) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("one bad round")
        await real(redis)

    monkeypatch.setattr(stream_metrics, "refresh_stream_gauges", flaky)
    task = asyncio.create_task(run_gauge_refresher(safe_redis, 0.05))
    await asyncio.sleep(0.4)
    task.cancel()
    assert calls >= 3 and not task.done() or task.cancelled()
    assert gauges() == (0, 0, 0)  # later rounds worked


# --- the done-when scenario with REAL processes -----------------------------------------------


async def _wait(check: Callable[[], Awaitable[bool]], within: float = 30) -> None:
    deadline = asyncio.get_event_loop().time() + within
    while asyncio.get_event_loop().time() < deadline:
        if await check():
            return
        await asyncio.sleep(0.15)
    raise AssertionError("condition not reached in time")


async def test_stopping_the_worker_makes_lag_grow_and_restarting_it_drains_everything(
    owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    """The PRD/TASKS acceptance test: scrape the API and the worker while a real worker process is
    run, stopped and started again."""
    link = await make_link(db, owner, "done001")
    worker_port = free_port()
    env = {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL,
        "REDIS_URL": TEST_REDIS_URL,
        "IP_HASH_SECRET": TEST_IP_HASH_SECRET,
        "ENV": "test",
        "WORKER_BLOCK_MS": "100",
        "WORKER_BATCH_SIZE": "10",
        "WORKER_CLAIM_IDLE_MS": "300",
        "WORKER_CLAIM_INTERVAL_SECONDS": "1",
        "WORKER_METRICS_PORT": str(worker_port),
        "METRICS_REFRESH_SECONDS": "0.2",
    }

    async def start_worker() -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.worker",
            cwd=BACKEND_DIR,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def stream_gauges(port: int) -> tuple[float, float, float]:
        s = parse_scrape(await scrape(port))
        return (
            s[("click_stream_length", ())],
            s[("click_stream_lag", ())],
            s[("click_stream_pending", ())],
        )

    async def add_clicks(n: int) -> None:
        pipe = raw.pipeline(transaction=False)
        for _ in range(n):
            pipe.xadd(
                STREAM,
                {
                    "event_id": str(uuid.uuid4()),
                    "link_id": str(link.id),
                    "code": "done001",
                    "ts": datetime.now(UTC).isoformat(),
                    "ip_hash": "h" * 64,
                    "user_agent": HUMAN_UA,
                    "referrer_host": "",
                },
            )
        await pipe.execute()

    async with running_app(settings_with(metrics_port=0, metrics_refresh_seconds=0.2)) as (app, _):
        api_port = app.state.metrics_port

        # 1. a running worker keeps up: lag and pending return to 0.
        worker = await start_worker()
        try:
            await _wait(lambda: _worker_up(worker_port))
            await add_clicks(10)
            await _wait(lambda: _rows(db, 10))
            await _wait(lambda: _gauges_are(stream_gauges, api_port, lag=0, pending=0))
            assert (await stream_gauges(api_port))[0] == 10  # length
            worker_samples = parse_scrape(await scrape(worker_port))
            assert worker_samples[("click_events_processed_total", (("status", "ok"),))] == 10
            worker_text = await scrape(worker_port)
            for name in ALL_NAMES:  # the worker endpoint lists the full name list as well
                assert f"# TYPE {name} " in worker_text, name
        finally:
            worker.send_signal(signal.SIGTERM)
            assert await asyncio.wait_for(worker.wait(), timeout=15) == 0

        # 2. worker stopped: new clicks pile up as LAG, visible on the API's endpoint.
        await add_clicks(25)
        await _wait(lambda: _gauges_are(stream_gauges, api_port, lag=25, pending=0))
        assert (await stream_gauges(api_port))[0] == 35

        # 3. PENDING grows when a worker receives messages and does not finish them.
        engine = create_engine(settings_with(), pool_size=1, max_overflow=0)
        settings = settings_with(worker_block_ms=50, worker_batch_size=10)
        redis = create_worker_redis(settings)
        stuck = ClickConsumer(redis, create_sessionmaker(engine), settings, consumer_name="stuck")
        try:
            assert len(await stuck.read_batch()) == 10  # received, never acknowledged
            await _wait(lambda: _gauges_are(stream_gauges, api_port, lag=15, pending=10))
        finally:
            await redis.aclose()
            await engine.dispose()

        # 4. a restarted worker drains lag AND the stuck pending messages.
        worker = await start_worker()
        try:
            await _wait(lambda: _rows(db, 35), within=40)
            await _wait(lambda: _gauges_are(stream_gauges, api_port, lag=0, pending=0), within=30)
        finally:
            worker.send_signal(signal.SIGTERM)
            assert await asyncio.wait_for(worker.wait(), timeout=15) == 0
    assert len(await events(db)) == 35  # nothing lost, nothing stored twice


async def _worker_up(port: int) -> bool:
    try:
        await scrape(port)
        return True
    except (httpx.ConnectError, httpx.ReadError):
        return False


async def _rows(db: AsyncSession, n: int) -> bool:
    return len(await events(db)) == n


async def _gauges_are(
    stream_gauges: Callable[[int], Awaitable[tuple[float, float, float]]],
    port: int,
    *,
    lag: float,
    pending: float,
) -> bool:
    _, got_lag, got_pending = await stream_gauges(port)
    return got_lag == lag and got_pending == pending


_ = (async_sessionmaker,)  # imported for annotations in some editors
