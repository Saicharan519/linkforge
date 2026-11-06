"""The worker's batch processing and consumer (task P3.2, ARCHITECTURE §2.3). Real Postgres+Redis.

`process_batch` is exercised directly (no Redis) for the database rules; `ClickConsumer` is
exercised against a real Redis stream for the delivery rules.
"""

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
import redis.asyncio as aioredis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import create_engine, create_sessionmaker
from app.models import ClickEvent, Link, LinkDailyStat, User
from app.services.clicks import STREAM
from app.worker.consumer import (
    GROUP,
    ClickConsumer,
    MalformedMessage,
    parse_message,
    process_batch,
)
from app.worker.ua import UAInfo
from tests.conftest import TEST_DATABASE_URL, TEST_IP_HASH_SECRET, TEST_REDIS_URL
from tests.integration.conftest import BACKEND_DIR
from tests.integration.factories import make_link, make_user

T0 = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
# A real browser string: with the real classifier an EMPTY user agent counts as a bot, so tests
# that expect clicks to be counted must send a believable one.
HUMAN_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"


def settings_with(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": TEST_DATABASE_URL,
        "redis_url": TEST_REDIS_URL,
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
async def sm() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(settings_with(), pool_size=2, max_overflow=1)
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


def fields(
    link: Link | uuid.UUID,
    *,
    event_id: uuid.UUID | None = None,
    ts: datetime | str = T0,
    ua: str = HUMAN_UA,
    referrer: str = "",
) -> dict[str, str]:
    link_id = link.id if isinstance(link, Link) else link
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "link_id": str(link_id),
        "code": "abc1234",
        "ts": ts if isinstance(ts, str) else ts.isoformat(),
        "ip_hash": "h" * 64,
        "user_agent": ua,
        "referrer_host": referrer,
    }


def message(link: Link | uuid.UUID, msg_id: str = "1-0", **kw: object):  # type: ignore[no-untyped-def]
    return parse_message(msg_id, fields(link, **kw))  # type: ignore[arg-type]


async def events(db: AsyncSession) -> list[ClickEvent]:
    """All stored clicks, read on a FRESH connection (what another process would see)."""
    engine = create_engine(settings_with(), pool_size=1, max_overflow=0)
    try:
        async with create_sessionmaker(engine)() as fresh:
            return list((await fresh.execute(select(ClickEvent).order_by(ClickEvent.id))).scalars())
    finally:
        await engine.dispose()


async def stats(db: AsyncSession) -> dict[tuple[uuid.UUID, date], int]:
    engine = create_engine(settings_with(), pool_size=1, max_overflow=0)
    try:
        async with create_sessionmaker(engine)() as fresh:
            rows = (await fresh.execute(select(LinkDailyStat))).scalars()
            return {(r.link_id, r.day): r.clicks for r in rows}
    finally:
        await engine.dispose()


def bot_if_ua_says_bot(raw: str | None) -> UAInfo:
    return UAInfo("other" if raw == "bot" else "desktop", "TestBrowser", "TestOS", raw == "bot")


# --- parsing ----------------------------------------------------------------------------------


def test_parse_message_reads_every_field_and_turns_empty_strings_into_none() -> None:
    link_id, event_id = uuid.uuid4(), uuid.uuid4()
    m = parse_message("7-1", fields(link_id, event_id=event_id, ua="", referrer=""))
    assert (m.msg_id, m.event_id, m.link_id) == ("7-1", event_id, link_id)
    assert m.clicked_at == T0 and m.user_agent is None and m.referrer_host is None
    full = parse_message("7-2", fields(link_id, ua="UA", referrer="news.example.com"))
    assert (full.user_agent, full.referrer_host) == ("UA", "news.example.com")


@pytest.mark.parametrize("drop", ["event_id", "link_id", "ts", "ip_hash"])
def test_a_message_missing_a_required_field_is_malformed(drop: str) -> None:
    bad = fields(uuid.uuid4())
    del bad[drop]
    with pytest.raises(MalformedMessage):
        parse_message("1-0", bad)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", "not-a-uuid"),
        ("link_id", "12345"),
        ("ts", "yesterday"),
        ("ts", "2026-11-06T12:00:00"),  # no timezone: ambiguous, refused
        ("ip_hash", ""),
    ],
)
def test_a_message_with_an_unusable_value_is_malformed(field: str, value: str) -> None:
    bad = fields(uuid.uuid4())
    bad[field] = value
    with pytest.raises(MalformedMessage):
        parse_message("1-0", bad)


# --- process_batch: the database rules --------------------------------------------------------


async def test_one_click_becomes_one_row_and_one_daily_count(
    sm: async_sessionmaker[AsyncSession], owner: User, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "one0001")
    msg = message(link, ua="UA/1", referrer="news.example.com")
    result = await process_batch(sm, [msg])

    assert (result.inserted, result.duplicates, result.orphans) == (1, 0, 0)
    assert result.ack_ids == ["1-0"]
    (row,) = await events(db)
    assert (row.event_id, row.link_id, row.clicked_at) == (msg.event_id, link.id, T0)
    assert (row.ip_hash, row.user_agent, row.referrer_host) == (
        "h" * 64,
        "UA/1",
        "news.example.com",
    )
    assert row.is_bot is False
    assert await stats(db) == {(link.id, T0.date()): 1}


async def test_a_repeated_event_in_one_batch_is_stored_once(
    sm: async_sessionmaker[AsyncSession], owner: User, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "dup0001")
    same = uuid.uuid4()
    result = await process_batch(
        sm, [message(link, "1-0", event_id=same), message(link, "2-0", event_id=same)]
    )
    assert (result.inserted, result.duplicates) == (1, 1)
    assert result.ack_ids == ["1-0", "2-0"]  # both are finished, both get acknowledged
    assert len(await events(db)) == 1
    assert await stats(db) == {(link.id, T0.date()): 1}


async def test_a_redelivered_batch_changes_nothing(
    sm: async_sessionmaker[AsyncSession], owner: User, db: AsyncSession
) -> None:
    """The same messages processed twice (at-least-once delivery): still one row, still +1."""
    link = await make_link(db, owner, "redel01")
    batch = [message(link, "1-0"), message(link, "2-0")]
    first = await process_batch(sm, batch)
    second = await process_batch(sm, batch)
    assert (first.inserted, first.duplicates) == (2, 0)
    assert (second.inserted, second.duplicates) == (0, 2)
    assert len(await events(db)) == 2
    assert await stats(db) == {(link.id, T0.date()): 2}  # NOT 4


async def test_an_orphan_is_dropped_and_the_others_in_the_batch_are_stored(
    sm: async_sessionmaker[AsyncSession], owner: User, db: AsyncSession
) -> None:
    keep = await make_link(db, owner, "keep001")
    gone = await make_link(db, owner, "gone001")
    messages = [
        message(keep, "1-0"),
        message(gone, "2-0"),
        message(keep, "3-0"),
        message(uuid.uuid4(), "4-0"),  # a link that never existed
    ]
    await db.delete(gone)
    await db.commit()

    result = await process_batch(sm, messages)
    assert (result.inserted, result.duplicates, result.orphans) == (2, 0, 2)
    assert result.ack_ids == ["1-0", "2-0", "3-0", "4-0"]  # orphans are finished too
    assert {e.link_id for e in await events(db)} == {keep.id}
    assert await stats(db) == {(keep.id, T0.date()): 2}


async def test_a_bot_is_stored_but_never_counted(
    sm: async_sessionmaker[AsyncSession], owner: User, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "bot0001")
    await process_batch(
        sm, [message(link, "1-0", ua="bot"), message(link, "2-0", ua="human")], bot_if_ua_says_bot
    )
    rows = {e.user_agent: e for e in await events(db)}
    assert rows["bot"].is_bot is True and rows["human"].is_bot is False
    assert (rows["bot"].device_type, rows["human"].device_type) == ("other", "desktop")
    assert (rows["human"].browser, rows["human"].os) == ("TestBrowser", "TestOS")
    assert await stats(db) == {(link.id, T0.date()): 1}  # the bot is excluded from the count


async def test_only_bots_in_a_batch_create_no_counter_row(
    sm: async_sessionmaker[AsyncSession], owner: User, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "bots001")
    await process_batch(sm, [message(link, ua="bot")], bot_if_ua_says_bot)
    assert len(await events(db)) == 1
    assert await stats(db) == {}


async def test_counts_group_by_link_and_by_utc_day(
    sm: async_sessionmaker[AsyncSession], owner: User, db: AsyncSession
) -> None:
    a = await make_link(db, owner, "grp0001")
    b = await make_link(db, owner, "grp0002")
    day1 = datetime(2026, 11, 6, 23, 59, 59, 999000, tzinfo=UTC)
    day2 = datetime(2026, 11, 7, 0, 0, 0, tzinfo=UTC)
    # 01:30 at UTC+2 on the 7th is 23:30 UTC on the 6th: the UTC day is what counts.
    offset = "2026-11-07T01:30:00+02:00"
    await process_batch(
        sm,
        [
            message(a, "1-0", ts=day1),
            message(a, "2-0", ts=day2),
            message(a, "3-0", ts=offset),
            message(a, "4-0", ts=day1),
            message(b, "5-0", ts=day1),
        ],
    )
    assert await stats(db) == {
        (a.id, date(2026, 11, 6)): 3,  # day1 twice + the +02:00 click
        (a.id, date(2026, 11, 7)): 1,
        (b.id, date(2026, 11, 6)): 1,
    }


async def test_counts_are_added_to_an_existing_counter(
    sm: async_sessionmaker[AsyncSession], owner: User, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "inc0001")
    await process_batch(sm, [message(link, "1-0"), message(link, "2-0")])
    await process_batch(sm, [message(link, "3-0")])  # a later batch, same day
    assert await stats(db) == {(link.id, T0.date()): 3}


async def test_an_empty_batch_does_nothing(
    sm: async_sessionmaker[AsyncSession], db: AsyncSession
) -> None:
    result = await process_batch(sm, [])
    assert (result.inserted, result.duplicates, result.orphans, result.ack_ids) == (0, 0, 0, [])


async def test_a_whole_batch_is_one_transaction(
    sm: async_sessionmaker[AsyncSession], owner: User, db: AsyncSession
) -> None:
    """If anything in the batch fails, NOTHING is stored (no rows without counters)."""
    link = await make_link(db, owner, "atom001")

    def exploding_parser(raw: str | None) -> UAInfo:
        if raw == "boom":
            raise RuntimeError("parser failed")
        return UAInfo(None, None, None, False)

    with pytest.raises(RuntimeError):
        await process_batch(
            sm, [message(link, "1-0", ua="fine"), message(link, "2-0", ua="boom")], exploding_parser
        )
    assert await events(db) == []
    assert await stats(db) == {}


# --- the consumer: delivery rules on a real Redis stream --------------------------------------


@pytest.fixture
async def consumer(
    sm: async_sessionmaker[AsyncSession], raw: aioredis.Redis
) -> AsyncIterator[ClickConsumer]:
    from app.worker.consumer import create_worker_redis

    settings = settings_with(worker_block_ms=100, worker_batch_size=100)
    redis = create_worker_redis(settings)
    yield ClickConsumer(redis, sm, settings, consumer_name="test-consumer")
    await redis.aclose()


async def add(raw: aioredis.Redis, link: Link | uuid.UUID, **kw: object) -> str:
    return await raw.xadd(STREAM, fields(link, **kw))  # type: ignore[arg-type]


async def test_the_group_is_created_at_zero_so_earlier_events_are_not_lost(
    consumer: ClickConsumer, owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "early01")
    await add(raw, link)  # queued BEFORE any group exists (the API ran before the worker)
    await add(raw, link)
    assert await raw.xinfo_groups(STREAM) == []

    await consumer.ensure_group()
    await consumer.ensure_group()  # idempotent: BUSYGROUP is not an error

    result = await consumer.run_once()
    assert result is not None and result.inserted == 2
    assert await stats(db) == {(link.id, T0.date()): 2}


async def test_the_group_and_stream_are_created_when_neither_exists(
    consumer: ClickConsumer, raw: aioredis.Redis
) -> None:
    assert await raw.exists(STREAM) == 0
    await consumer.ensure_group()
    (group,) = await raw.xinfo_groups(STREAM)
    assert group["name"] == GROUP
    assert await consumer.run_once() is None  # nothing to read: returns, does not hang


async def test_messages_are_stored_then_acknowledged(
    consumer: ClickConsumer, owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "ack0001")
    await consumer.ensure_group()
    for _ in range(3):
        await add(raw, link)
    result = await consumer.run_once()
    assert result is not None and result.inserted == 3
    assert (await raw.xpending(STREAM, GROUP))["pending"] == 0  # all acknowledged
    assert len(await events(db)) == 3


async def test_the_ack_happens_only_after_the_rows_are_committed(
    consumer: ClickConsumer,
    owner: User,
    raw: aioredis.Redis,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When XACK is called the rows must already be visible to ANOTHER connection."""
    link = await make_link(db, owner, "order01")
    await consumer.ensure_group()
    await add(raw, link)
    await add(raw, link)

    seen_at_ack: list[int] = []
    original_xack = consumer.redis.xack

    async def checking_xack(*args: object, **kwargs: object) -> int:
        seen_at_ack.append(len(await events(db)))  # fresh connection: COMMITTED rows only
        return await original_xack(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(consumer.redis, "xack", checking_xack)
    await consumer.run_once()
    assert seen_at_ack == [2]  # committed (visible) before the acknowledgement


async def test_nothing_is_acknowledged_when_the_database_is_unavailable(
    owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    from app.worker.consumer import create_worker_redis

    link = await make_link(db, owner, "down001")
    dead = create_engine(
        settings_with(database_url="postgresql+asyncpg://nobody:nopass@127.0.0.1:1/none"),
        pool_size=1,
        max_overflow=0,
    )
    settings = settings_with(worker_block_ms=100)
    redis = create_worker_redis(settings)
    broken = ClickConsumer(redis, create_sessionmaker(dead), settings, consumer_name="c-down")
    try:
        await broken.ensure_group()
        await add(raw, link)
        with pytest.raises(OSError):
            await broken.run_once()
        pending = await raw.xpending(STREAM, GROUP)
        assert pending["pending"] == 1  # delivered, not acknowledged: it will be redelivered
        assert await events(db) == []
    finally:
        await redis.aclose()
        await dead.dispose()


async def test_orphans_are_acknowledged_and_the_rest_stored(
    consumer: ClickConsumer, owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "orph001")
    await consumer.ensure_group()
    await add(raw, link)
    await add(raw, uuid.uuid4())  # a link that does not exist
    await add(raw, link)
    result = await consumer.run_once()
    assert result is not None and (result.inserted, result.orphans) == (2, 1)
    assert (await raw.xpending(STREAM, GROUP))["pending"] == 0


async def test_a_malformed_message_does_not_block_the_valid_ones(
    consumer: ClickConsumer, owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "mal0001")
    await consumer.ensure_group()
    await add(raw, link)
    await raw.xadd(STREAM, {"event_id": "nope"})
    await add(raw, link)
    result = await consumer.run_once()
    assert result is not None and result.inserted == 2 and result.dead == 1
    # A message that can never parse goes straight to the dead letters and is acknowledged
    # (details in test_worker_failures.py).
    assert (await raw.xpending(STREAM, GROUP))["pending"] == 0
    assert await raw.xlen("clicks:dead") == 1


async def test_a_big_backlog_is_worked_off_in_batches(
    owner: User, raw: aioredis.Redis, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    from app.worker.consumer import create_worker_redis

    link = await make_link(db, owner, "big0001")
    settings = settings_with(worker_block_ms=100, worker_batch_size=10)
    redis = create_worker_redis(settings)
    consumer = ClickConsumer(redis, sm, settings, consumer_name="batcher")
    try:
        await consumer.ensure_group()
        for _ in range(35):
            await add(raw, link)
        sizes = []
        while (result := await consumer.run_once()) is not None:
            sizes.append(result.inserted)
        assert sizes == [10, 10, 10, 5]
        assert await stats(db) == {(link.id, T0.date()): 35}
    finally:
        await redis.aclose()


async def test_two_consumers_in_one_group_never_share_a_message(
    owner: User, raw: aioredis.Redis, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    from app.worker.consumer import create_worker_redis

    link = await make_link(db, owner, "two0001")
    settings = settings_with(worker_block_ms=100, worker_batch_size=5)
    r1, r2 = create_worker_redis(settings), create_worker_redis(settings)
    c1 = ClickConsumer(r1, sm, settings, consumer_name="pod-a")
    c2 = ClickConsumer(r2, sm, settings, consumer_name="pod-b")
    try:
        await c1.ensure_group()
        for _ in range(10):
            await add(raw, link)
        first, second = await c1.run_once(), await c2.run_once()
        assert first is not None and second is not None
        assert first.inserted == 5 and second.inserted == 5  # split, not duplicated
        assert await stats(db) == {(link.id, T0.date()): 10}
    finally:
        await r1.aclose()
        await r2.aclose()


# --- the real process -------------------------------------------------------------------------


async def test_the_worker_process_stores_a_queued_click(
    owner: User, raw: aioredis.Redis, db: AsyncSession
) -> None:
    """End to end: `python -m app.worker` as a separate process, fed through the real stream."""
    link = await make_link(db, owner, "proc001")
    env = {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL,
        "REDIS_URL": TEST_REDIS_URL,
        "IP_HASH_SECRET": TEST_IP_HASH_SECRET,
        "WORKER_BLOCK_MS": "200",
        "ENV": "test",
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.worker",
        cwd=BACKEND_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        await add(raw, link)  # a person
        await add(raw, link, ua="curl/8.5.0")  # a script: stored, never counted
        deadline = asyncio.get_event_loop().time() + 20
        stored = 0
        while asyncio.get_event_loop().time() < deadline and stored < 2:
            await asyncio.sleep(0.3)
            stored = len(await events(db))
        assert stored == 2, "the worker process did not store the clicks in time"
        assert await stats(db) == {(link.id, T0.date()): 1}  # only the person
        by_agent = {e.user_agent: e for e in await events(db)}
        assert by_agent["curl/8.5.0"].is_bot is True
        human = by_agent[HUMAN_UA]
        assert (human.is_bot, human.device_type, human.browser, human.os) == (
            False,
            "desktop",
            "Firefox",
            "Linux",
        )
        assert (await raw.xpending(STREAM, GROUP))["pending"] == 0
        assert proc.returncode is None  # still running
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except TimeoutError:
            proc.kill()


async def test_the_clicks_table_has_no_rows_without_a_link(db: AsyncSession) -> None:
    # Guards the foreign key the orphan pre-filter relies on (ON DELETE CASCADE, ARCH §3).
    await db.rollback()
    row = (
        await db.execute(
            text(
                "select confdeltype from pg_constraint "
                "where conrelid='click_events'::regclass and contype='f'"
            )
        )
    ).scalar_one()
    assert row in ("c", b"c")  # c = ON DELETE CASCADE
