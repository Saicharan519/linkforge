"""How the worker behaves when things go wrong (task P3.3, ARCHITECTURE §2.3).

Every failure here is produced for real, against real Postgres and Redis:
- a poison event: a user agent containing a NUL byte, which Postgres genuinely refuses;
- a link deleted between the orphan check and the insert: a second connection deletes it inside
  the exact window (the `_after_prefilter` hook);
- a failing dead-letter write: `clicks:dead` is made a plain string, so XADD really errors;
- a Redis wipe: the stream key is deleted, taking the consumer group with it;
- a crash after the commit: the batch is committed and the acknowledgement is simply not sent;
- real processes killed with SIGKILL and stopped with SIGTERM.
"""

import asyncio
import os
import signal
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
import redis.asyncio as aioredis
from prometheus_client import REGISTRY
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import create_engine, create_sessionmaker
from app.models import Link, User
from app.services.clicks import STREAM
from app.worker import consumer as consumer_module
from app.worker.consumer import (
    DEAD_STREAM,
    GROUP,
    ClickConsumer,
    create_worker_redis,
    parse_message,
    process_batch,
)
from app.worker.ua import UAInfo
from tests.conftest import TEST_DATABASE_URL, TEST_IP_HASH_SECRET, TEST_REDIS_URL
from tests.integration.conftest import BACKEND_DIR
from tests.integration.factories import make_link, make_user
from tests.integration.test_worker_batch import T0, add, events, fields, stats

DEAD_DB = "postgresql+asyncpg://nobody:nopass@127.0.0.1:1/none"
NUL_UA = "agent\x00with-nul-byte"  # Postgres text cannot store NUL: a real poison message


def settings_with(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": TEST_DATABASE_URL,
        "redis_url": TEST_REDIS_URL,
        # Small timings so reclaiming does not take a minute: a message is "idle" after 50 ms.
        "worker_block_ms": 50,
        "worker_claim_idle_ms": 50,
        "worker_claim_interval_seconds": 0,
        "max_deliveries": 3,
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]


def processed(status: str) -> float:
    return REGISTRY.get_sample_value("click_events_processed_total", {"status": status}) or 0.0


@pytest.fixture
async def sm() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(settings_with(), pool_size=3, max_overflow=2)
    yield create_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
async def owner(db: AsyncSession) -> User:
    return await make_user(db, "owner@example.com")


@pytest.fixture
async def raw() -> AsyncIterator[aioredis.Redis]:
    r = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def make_consumer(
    sm: async_sessionmaker[AsyncSession],
) -> AsyncIterator[Callable[..., ClickConsumer]]:
    clients: list[aioredis.Redis] = []

    def factory(
        name: str, parse_ua: Callable[[str | None], UAInfo] | None = None, **overrides: object
    ) -> ClickConsumer:
        settings = settings_with(**overrides)
        redis = create_worker_redis(settings)
        clients.append(redis)
        if parse_ua is None:
            return ClickConsumer(redis, sm, settings, consumer_name=name)
        return ClickConsumer(redis, sm, settings, consumer_name=name, parse_ua=parse_ua)

    yield factory
    for client in clients:
        await client.aclose()


async def pending_ids(raw: aioredis.Redis) -> list[str]:
    info = await raw.xpending(STREAM, GROUP)
    if info["pending"] == 0:
        return []
    return [p["message_id"] for p in await raw.xpending_range(STREAM, GROUP, "-", "+", 1000)]


async def wait_idle(raw: aioredis.Redis, min_idle_ms: int = 60) -> None:
    """Wait until every pending message has been idle for `min_idle_ms` (Redis's own idle clock).

    The tests use worker_claim_idle_ms=50. A fixed `asyncio.sleep(0.1)` assumed that 0.1 s of
    wall-clock time is always more than 50 ms of idle time; this asks Redis instead, so a slow
    machine cannot make a reclaim() find nothing to take over (it failed once in a full-suite
    run and was not reproducible in 36 reruns, so the cause is a best guess).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while True:
        rows = await raw.xpending_range(STREAM, GROUP, "-", "+", 1000)
        if all(r["time_since_delivered"] >= min_idle_ms for r in rows):
            return
        assert loop.time() < deadline, f"pending entries never became idle: {rows}"
        await asyncio.sleep(0.02)


# --- a crash between the commit and the acknowledgement (the REQUIRED test) -------------------


async def test_commit_then_crash_before_xack_is_redelivered_without_double_counting(
    make_consumer: Callable[..., ClickConsumer],
    sm: async_sessionmaker[AsyncSession],
    owner: User,
    raw: aioredis.Redis,
    db: AsyncSession,
) -> None:
    link = await make_link(db, owner, "crash01")
    pod_a, pod_b = make_consumer("pod-a"), make_consumer("pod-b")
    await pod_a.ensure_group()
    await add(raw, link)
    await add(raw, link)

    # pod-a receives both messages, COMMITS them... and dies before sending XACK.
    entries = await pod_a.read_batch()
    await process_batch(sm, [parse_message(i, f) for i, f in entries])
    assert len(await pending_ids(raw)) == 2  # still pending: nobody acknowledged
    assert len(await events(db)) == 2
    assert await stats(db) == {(link.id, T0.date()): 2}

    duplicates = processed("duplicate")
    await wait_idle(raw)  # let them become idle (Redis's clock, not ours)
    pending_before = await raw.xpending_range(STREAM, GROUP, "-", "+", 10)
    claimed = await pod_b.reclaim()  # pod-b takes them over and processes them again
    assert claimed == 2, f"reclaim claimed {claimed}; pending before it: {pending_before}"

    assert await pending_ids(raw) == []  # now acknowledged
    assert len(await events(db)) == 2  # still exactly two rows
    assert await stats(db) == {(link.id, T0.date()): 2}  # NOT 4: the rollup was not repeated
    assert processed("duplicate") == duplicates + 2


# --- a failing batch: one bad event must not block the good ones ------------------------------


async def test_a_failing_batch_falls_back_to_one_message_at_a_time(
    make_consumer: Callable[..., ClickConsumer], owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "fall001")

    def parser(ua: str | None) -> UAInfo:
        if ua == "boom":
            raise RuntimeError("cannot parse this one")
        return UAInfo(None, None, None, False)

    consumer = make_consumer("pod-a", parse_ua=parser)
    await consumer.ensure_group()
    ids = [await add(raw, link, ua=ua) for ua in ("ok1", "ok2", "boom", "ok3", "ok4")]
    result = await consumer.run_once()

    assert result is not None and result.inserted == 4  # the other four were stored
    assert len(await events(db)) == 4
    assert await stats(db) == {(link.id, T0.date()): 4}
    assert await pending_ids(raw) == [ids[2]]  # only the failing one waits for another try


async def test_a_nul_byte_user_agent_is_a_real_poison_message_that_fails_alone(
    make_consumer: Callable[..., ClickConsumer], owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "nul0001")
    consumer = make_consumer("pod-a")
    await consumer.ensure_group()
    await add(raw, link, ua="fine-1")
    poison_id = await add(raw, link, ua=NUL_UA)
    await add(raw, link, ua="fine-2")
    result = await consumer.run_once()
    assert result is not None and result.inserted == 2
    assert {e.user_agent for e in await events(db)} == {"fine-1", "fine-2"}
    assert await pending_ids(raw) == [poison_id]


# --- poison messages end up in the dead letters -----------------------------------------------


async def test_a_poison_message_is_dead_lettered_after_max_deliveries(
    make_consumer: Callable[..., ClickConsumer], owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "poison1")
    consumer = make_consumer("pod-a")  # max_deliveries = 3
    await consumer.ensure_group()
    await add(raw, link, ua="fine")
    poison_id = await add(raw, link, ua=NUL_UA)
    await consumer.run_once()  # delivery 1: the good one is stored, the poison one fails
    dead_before = processed("dead")

    await wait_idle(raw)
    await consumer.reclaim()  # delivery 2: fails again, still pending
    assert await pending_ids(raw) == [poison_id]
    assert await raw.xlen(DEAD_STREAM) == 0

    await wait_idle(raw)
    await consumer.reclaim()  # delivery 3 = MAX_DELIVERIES: given up
    assert await pending_ids(raw) == []  # acknowledged, because the dead-letter write succeeded
    ((_, record),) = await raw.xrange(DEAD_STREAM)
    assert record["original_id"] == poison_id
    assert record["user_agent"] == NUL_UA and record["code"] == "abc1234"  # the original fields
    assert "gave up after 3 deliveries" in record["error"]
    assert datetime.fromisoformat(record["failed_at"]).tzinfo is not None
    assert processed("dead") == dead_before + 1
    assert [e.user_agent for e in await events(db)] == ["fine"]  # the good click was never lost
    assert await stats(db) == {(link.id, T0.date()): 1}


async def test_the_dead_letter_is_acknowledged_only_after_its_write_succeeded(
    make_consumer: Callable[..., ClickConsumer], owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "deadw01")
    consumer = make_consumer("pod-a", max_deliveries=2)
    await consumer.ensure_group()
    poison_id = await add(raw, link, ua=NUL_UA)
    await consumer.run_once()

    await raw.set(DEAD_STREAM, "I am a string, not a stream")  # XADD to it now really fails
    await wait_idle(raw)
    await consumer.reclaim()
    assert await pending_ids(raw) == [poison_id]  # NOT acknowledged: nothing may be lost silently
    assert await raw.get(DEAD_STREAM) == "I am a string, not a stream"

    await raw.delete(DEAD_STREAM)  # the problem is fixed
    await wait_idle(raw)
    await consumer.reclaim()
    assert await pending_ids(raw) == []
    assert [r["original_id"] for _, r in await raw.xrange(DEAD_STREAM)] == [poison_id]


async def test_a_malformed_message_is_dead_lettered_at_once_and_the_rest_are_stored(
    make_consumer: Callable[..., ClickConsumer], owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "malf001")
    consumer = make_consumer("pod-a")
    await consumer.ensure_group()
    await add(raw, link)
    bad_id = await raw.xadd(STREAM, {"event_id": "not-a-uuid", "link_id": "x"})
    await add(raw, link)
    result = await consumer.run_once()

    assert result is not None and (result.inserted, result.dead) == (2, 1)
    ((_, record),) = await raw.xrange(DEAD_STREAM)
    assert record["original_id"] == bad_id and record["event_id"] == "not-a-uuid"
    assert record["error"].startswith("malformed:")
    assert await pending_ids(raw) == []


async def test_a_malformed_message_stays_pending_while_the_dead_letter_write_fails(
    make_consumer: Callable[..., ClickConsumer], owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "malf002")
    consumer = make_consumer("pod-a")
    await consumer.ensure_group()
    await raw.set(DEAD_STREAM, "blocked")
    bad_id = await raw.xadd(STREAM, {"nonsense": "1"})
    await add(raw, link)
    result = await consumer.run_once()
    assert result is not None and result.inserted == 1 and result.dead == 0
    assert await pending_ids(raw) == [bad_id]  # kept, not dropped


# --- a link deleted between the orphan check and the insert -----------------------------------


async def test_a_link_deleted_mid_batch_is_an_orphan_and_the_others_survive(
    make_consumer: Callable[..., ClickConsumer],
    sm: async_sessionmaker[AsyncSession],
    owner: User,
    raw: aioredis.Redis,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keep = await make_link(db, owner, "keep001")
    gone1 = await make_link(db, owner, "gone001")
    gone2 = await make_link(db, owner, "gone002")
    doomed = [gone1.id, gone2.id]
    real_process = consumer_module.process_batch
    killer = create_sessionmaker(create_engine(settings_with(), pool_size=1, max_overflow=0))

    def deleter_for(targets: list[uuid.UUID]) -> Callable[[], Awaitable[None]]:
        async def delete_first_surviving_target() -> None:
            """Runs between the orphan check and the insert, on ANOTHER connection: the link is
            gone by the time the insert's foreign key is checked. Only a link that THIS call is
            about is touched, so the one-by-one retry of gone2 is raced as well."""
            async with killer() as other, other.begin():
                remaining = [i for i in targets if await other.get(Link, i) is not None]
                if remaining:
                    await other.execute(delete(Link).where(Link.id == remaining[0]))

        return delete_first_surviving_target

    async def racing_process(sessionmaker, messages, parse_ua=None, **kw):  # type: ignore[no-untyped-def]
        targets = [m.link_id for m in messages if m.link_id in doomed]
        hook = deleter_for(targets) if targets else None
        extra = {"parse_ua": parse_ua} if parse_ua else {}
        return await real_process(sessionmaker, messages, **extra, _after_prefilter=hook)

    monkeypatch.setattr(consumer_module, "process_batch", racing_process)
    consumer = make_consumer("pod-a")
    await consumer.ensure_group()
    await add(raw, keep)
    await add(raw, gone1)
    await add(raw, gone2)
    await add(raw, keep)

    orphans = processed("orphan")
    result = await consumer.run_once()
    # Whole batch: FK violation (gone1 deleted after the check). Then one by one: keep, keep ok;
    # gone1 is an orphan at the check; gone2 exists at the check, is deleted, FK violation again.
    assert result is not None and result.inserted == 2 and result.orphans == 2
    assert processed("orphan") == orphans + 2
    assert {e.link_id for e in await events(db)} == {keep.id}
    assert await pending_ids(raw) == []  # orphans are finished, not stuck
    assert await stats(db) == {(keep.id, T0.date()): 2}


# --- the database is down: not the messages' fault ---------------------------------------------


async def test_when_the_database_is_down_messages_are_not_retried_one_by_one(
    owner: User, raw: aioredis.Redis, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = await make_link(db, owner, "down001")
    dead_engine = create_engine(settings_with(database_url=DEAD_DB), pool_size=1, max_overflow=0)
    settings = settings_with()
    redis = create_worker_redis(settings)
    consumer = ClickConsumer(redis, create_sessionmaker(dead_engine), settings, consumer_name="c")
    calls = 0
    real_process = consumer_module.process_batch

    async def counting(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return await real_process(*args, **kwargs)

    monkeypatch.setattr(consumer_module, "process_batch", counting)
    try:
        await consumer.ensure_group()
        for _ in range(20):
            await add(raw, link)
        with pytest.raises(OSError):
            await consumer.run_once()
        assert calls == 1  # ONE attempt for the batch, not 20 more (each would wait for a timeout)
        assert len(await pending_ids(raw)) == 20  # all still pending: nothing lost, nothing acked
    finally:
        await redis.aclose()
        await dead_engine.dispose()


async def test_what_was_stored_before_the_database_went_away_is_still_acknowledged(
    make_consumer: Callable[..., ClickConsumer],
    owner: User,
    raw: aioredis.Redis,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """During the one-by-one fallback the connection drops after two messages were committed. Those
    two are finished and must be acknowledged (or they would be stored again on redelivery); the
    rest stay pending."""
    link = await make_link(db, owner, "part001")
    consumer = make_consumer("pod-a")
    await consumer.ensure_group()
    # The poison NUL byte makes the whole batch fail, which starts the one-by-one fallback.
    ids = [await add(raw, link, ua=ua) for ua in (NUL_UA, "ok-1", "ok-2", "ok-3", "ok-4")]
    real_process = consumer_module.process_batch
    calls = 0

    async def connection_lost_on_the_fourth_call(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 4:  # call 1 = the batch, 2 = poison alone, 3 = ok-1, 4 = ok-2 ... see below
            raise ConnectionResetError("database connection lost")
        return await real_process(*args, **kwargs)

    monkeypatch.setattr(consumer_module, "process_batch", connection_lost_on_the_fourth_call)
    with pytest.raises(OSError):
        await consumer.run_once()

    stored = {e.user_agent for e in await events(db)}
    assert stored == {"ok-1"}  # committed before the connection dropped
    # ok-1 was acknowledged; the poison one, ok-2 (interrupted), ok-3 and ok-4 are still pending.
    assert await pending_ids(raw) == [ids[0], ids[2], ids[3], ids[4]]


# --- reclaiming ---------------------------------------------------------------------------------


async def test_messages_that_are_not_idle_yet_are_left_to_their_owner(
    make_consumer: Callable[..., ClickConsumer], owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "idle001")
    pod_a = make_consumer("pod-a")
    pod_b = make_consumer("pod-b", worker_claim_idle_ms=60000)  # the production default
    await pod_a.ensure_group()
    await add(raw, link)
    await pod_a.read_batch()  # delivered to pod-a, being worked on
    assert await pod_b.reclaim() == 0  # pod-a is probably still busy: hands off
    owners = {p["consumer"] for p in await raw.xpending_range(STREAM, GROUP, "-", "+", 10)}
    assert owners == {"pod-a"}


async def test_reclaiming_with_nothing_pending_does_nothing(
    make_consumer: Callable[..., ClickConsumer], raw: aioredis.Redis
) -> None:
    consumer = make_consumer("pod-a")
    await consumer.ensure_group()
    assert await consumer.reclaim() == 0


# --- Redis wiped -------------------------------------------------------------------------------


async def test_the_worker_recovers_when_redis_loses_the_stream_and_the_group(
    make_consumer: Callable[..., ClickConsumer], owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "wipe001")
    consumer = make_consumer("pod-a")
    stop = asyncio.Event()
    task = asyncio.create_task(consumer.run_forever(stop))
    try:
        await add(raw, link)
        await _wait_until(lambda: _count(db, 1))
        assert len(await raw.xinfo_groups(STREAM)) == 1

        await raw.delete(STREAM)  # the stream AND its consumer group are gone (NOGROUP now)
        assert await raw.xinfo_groups(STREAM) if await raw.exists(STREAM) else True
        await add(raw, link)  # new clicks arrive into a brand-new stream
        await add(raw, link)
        await _wait_until(lambda: _count(db, 3))  # ...and are processed after the group is rebuilt
        assert len(await raw.xinfo_groups(STREAM)) == 1
        assert await stats(db) == {(link.id, T0.date()): 3}
        assert not task.done()  # it never crashed
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)


async def _count(db: AsyncSession, expected: int) -> bool:
    return len(await events(db)) == expected


async def _wait_until(check: Callable[[], Awaitable[bool]], within: float = 15) -> None:
    deadline = asyncio.get_event_loop().time() + within
    while asyncio.get_event_loop().time() < deadline:
        if await check():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("condition not reached in time")


# --- stopping ---------------------------------------------------------------------------------


async def test_run_forever_returns_promptly_when_asked_to_stop(
    make_consumer: Callable[..., ClickConsumer],
) -> None:
    consumer = make_consumer("pod-a")
    stop = asyncio.Event()
    task = asyncio.create_task(consumer.run_forever(stop))
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, timeout=2)  # within one block time


# --- real processes: SIGKILL and SIGTERM ------------------------------------------------------


def worker_env(**extra: str) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL,
        "REDIS_URL": TEST_REDIS_URL,
        "IP_HASH_SECRET": TEST_IP_HASH_SECRET,
        "ENV": "test",
        "WORKER_BLOCK_MS": "100",
        "WORKER_BATCH_SIZE": "20",
        "WORKER_CLAIM_IDLE_MS": "300",
        "WORKER_CLAIM_INTERVAL_SECONDS": "1",
        **extra,
    }


async def start_worker() -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.worker",
        cwd=BACKEND_DIR,
        env=worker_env(),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


async def fill_stream(raw: aioredis.Redis, link: Link, n: int) -> None:
    pipe = raw.pipeline(transaction=False)
    for i in range(n):
        pipe.xadd(STREAM, fields(link, ts=T0 + timedelta(seconds=i)))
    await pipe.execute()


async def total_clicks(db: AsyncSession, link: Link) -> int:
    return sum((await stats(db)).values())


async def test_a_worker_killed_with_sigkill_loses_and_double_counts_nothing(
    owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    """A real crash at an arbitrary moment: another worker finishes the job and the totals are
    EXACT (no lost clicks, no clicks counted twice)."""
    link = await make_link(db, owner, "kill001")
    n = 400
    await fill_stream(raw, link, n)
    first = await start_worker()
    try:
        await _wait_until(lambda: _more_than(db, 0))
        first.send_signal(signal.SIGKILL)  # no chance to acknowledge anything
        await first.wait()
    finally:
        if first.returncode is None:
            first.kill()
    stored_at_crash = len(await events(db))
    assert 0 < stored_at_crash < n or stored_at_crash == n  # (a fast machine may have finished)

    second = await start_worker()
    try:
        await _wait_until(lambda: _count(db, n), within=40)
        await _wait_until(lambda: _no_pending(raw), within=20)
    finally:
        second.terminate()
        await asyncio.wait_for(second.wait(), timeout=15)
    assert len(await events(db)) == n  # nothing lost, nothing stored twice
    assert await total_clicks(db, link) == n  # and the counters match exactly


async def _more_than(db: AsyncSession, n: int) -> bool:
    return len(await events(db)) > n


async def _no_pending(raw: aioredis.Redis) -> bool:
    return (await raw.xpending(STREAM, GROUP))["pending"] == 0


async def test_sigterm_finishes_the_batch_acknowledges_it_and_exits_zero(
    owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "term001")
    n = 400
    await fill_stream(raw, link, n)
    worker = await start_worker()
    try:
        await _wait_until(lambda: _more_than(db, 0))
        worker.send_signal(signal.SIGTERM)
        code = await asyncio.wait_for(worker.wait(), timeout=15)
    finally:
        if worker.returncode is None:
            worker.kill()
    assert code == 0  # a clean exit
    stored = len(await events(db))
    assert await _no_pending(raw)  # every message that was read is finished AND acknowledged
    assert await total_clicks(db, link) == stored  # counters match the stored rows

    # A second worker completes the remainder: exactly n in total.
    second = await start_worker()
    try:
        await _wait_until(lambda: _count(db, n), within=40)
    finally:
        second.terminate()
        await asyncio.wait_for(second.wait(), timeout=15)
    assert await total_clicks(db, link) == n


async def test_the_worker_survives_a_poison_message_end_to_end(
    owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    """The whole machine as a real process: good clicks stored, the poison one dead-lettered."""
    link = await make_link(db, owner, "e2e0001")
    await raw.xadd(STREAM, fields(link, ua="good-1"))
    await raw.xadd(STREAM, fields(link, ua=NUL_UA))
    await raw.xadd(STREAM, fields(link, ua="good-2"))
    worker = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.worker",
        cwd=BACKEND_DIR,
        env=worker_env(WORKER_CLAIM_IDLE_MS="200", MAX_DELIVERIES="3"),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:

        async def dead_lettered() -> bool:
            return await raw.xlen(DEAD_STREAM) == 1

        await _wait_until(dead_lettered, within=30)
        await _wait_until(lambda: _no_pending(raw), within=10)
    finally:
        worker.terminate()
        await asyncio.wait_for(worker.wait(), timeout=15)
    assert {e.user_agent for e in await events(db)} == {"good-1", "good-2"}
    assert await total_clicks(db, link) == 2


_ = (uuid, UTC)  # imports used by helpers above in some branches
