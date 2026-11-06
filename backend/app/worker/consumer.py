"""The click consumer (ARCHITECTURE §2.3). Task P3.2: the happy path, batch by batch.

Delivery is AT LEAST ONCE: a message may be handed to a worker more than once (a crash after the
database commit but before the acknowledgement). Processing is therefore IDEMPOTENT: the unique
`event_id` makes a second insert a no-op, and the daily counters are only increased for rows that
were really inserted. The two rules together give "exactly once in the database" without any
distributed transaction.

One batch, in one Postgres transaction:
  1. drop orphans: clicks whose link was deleted meanwhile (nothing to attach them to);
  2. INSERT ... ON CONFLICT (event_id) DO NOTHING RETURNING ...: only NEW rows come back;
  3. add one to the daily counter of each (link, UTC day) for the new, non-bot rows only;
  4. COMMIT. Only AFTER the commit are the messages acknowledged (XACK).
Acknowledging before the commit would lose clicks if the worker died in between.
"""

import logging
import socket
import time
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.metrics import CLICK_BATCH_SECONDS, CLICKS_PROCESSED
from app.models import ClickEvent, Link, LinkDailyStat
from app.services.clicks import STREAM
from app.worker.ua import UAInfo, parse_user_agent

logger = logging.getLogger(__name__)

GROUP = "click-workers"


class MalformedMessage(Exception):
    """A stream entry that is missing a field or holds an unusable value."""


@dataclass(frozen=True, slots=True)
class ClickMessage:
    msg_id: str
    event_id: uuid.UUID
    link_id: uuid.UUID
    clicked_at: datetime
    ip_hash: str
    user_agent: str | None
    referrer_host: str | None


@dataclass(frozen=True, slots=True)
class BatchResult:
    inserted: int  # new rows
    duplicates: int  # already stored (a redelivery, or the same event twice)
    orphans: int  # their link no longer exists
    ack_ids: list[str]  # every message that is finished and may be acknowledged


def parse_message(msg_id: str, fields: Mapping[str, str]) -> ClickMessage:
    """Turn stream fields into a typed message, or raise MalformedMessage."""
    try:
        ts = datetime.fromisoformat(fields["ts"])
        if ts.tzinfo is None:
            raise ValueError("timestamp without timezone")
        ip_hash = fields["ip_hash"]
        if not ip_hash:
            raise ValueError("empty ip_hash")
        return ClickMessage(
            msg_id=msg_id,
            event_id=uuid.UUID(fields["event_id"]),
            link_id=uuid.UUID(fields["link_id"]),
            clicked_at=ts,
            ip_hash=ip_hash,
            user_agent=fields.get("user_agent") or None,  # "" means "not sent": stored as NULL
            referrer_host=fields.get("referrer_host") or None,
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise MalformedMessage(f"{type(exc).__name__}: {exc}") from None


async def process_batch(
    sessionmaker: async_sessionmaker[AsyncSession],
    messages: Sequence[ClickMessage],
    parse_ua: Callable[[str | None], UAInfo] = parse_user_agent,
) -> BatchResult:
    """Store a batch in ONE transaction (steps 1-4 of the module docstring). Does NOT touch Redis:
    the caller acknowledges the returned ids after this function has returned, i.e. after COMMIT."""
    if not messages:
        return BatchResult(0, 0, 0, [])

    async with sessionmaker() as session, session.begin():
        # 1. Orphan pre-filter: links deleted between the click and now.
        wanted = {m.link_id for m in messages}
        existing = set(
            (await session.execute(select(Link.id).where(Link.id.in_(wanted)))).scalars()
        )
        live = [m for m in messages if m.link_id in existing]

        inserted: list[tuple[uuid.UUID, datetime, bool]] = []
        if live:
            rows = []
            for m in live:
                ua = parse_ua(m.user_agent)
                rows.append(
                    {
                        "event_id": m.event_id,
                        "link_id": m.link_id,
                        "clicked_at": m.clicked_at,
                        "ip_hash": m.ip_hash,
                        "user_agent": m.user_agent,
                        "referrer_host": m.referrer_host,
                        "device_type": ua.device_type,
                        "browser": ua.browser,
                        "os": ua.os,
                        "is_bot": ua.is_bot,
                    }
                )
            # 2. Idempotent insert. RETURNING yields ONLY the rows that were really inserted: a
            # redelivered or repeated event conflicts on event_id and comes back as nothing.
            stmt = (
                pg_insert(ClickEvent)
                .values(rows)
                .on_conflict_do_nothing(index_elements=[ClickEvent.event_id])
                .returning(ClickEvent.link_id, ClickEvent.clicked_at, ClickEvent.is_bot)
            )
            inserted = [tuple(r) for r in (await session.execute(stmt)).all()]  # type: ignore[misc]

            # 3. Daily counters, from the NEW non-bot rows only. The day is the UTC calendar date
            # (the API reports UTC days and the frontend never converts them).
            per_day = Counter(
                (link_id, clicked_at.astimezone(UTC).date())
                for link_id, clicked_at, is_bot in inserted
                if not is_bot
            )
            if per_day:
                # Sorted: two workers updating the same rows always lock them in the same order,
                # so they can wait for each other but never deadlock.
                upsert = pg_insert(LinkDailyStat).values(
                    [
                        {"link_id": link_id, "day": day, "clicks": n}
                        for (link_id, day), n in sorted(per_day.items())
                    ]
                )
                await session.execute(
                    upsert.on_conflict_do_update(
                        index_elements=[LinkDailyStat.link_id, LinkDailyStat.day],
                        set_={"clicks": LinkDailyStat.clicks + upsert.excluded.clicks},
                    )
                )
        # 4. leaving the `with` block COMMITS.

    return BatchResult(
        inserted=len(inserted),
        duplicates=len(live) - len(inserted),
        orphans=len(messages) - len(live),
        ack_ids=[m.msg_id for m in messages],
    )


def create_worker_redis(settings: Settings) -> Redis:
    """A Redis client for the worker. Unlike the API's (100 ms bound), its socket timeout must
    EXCEED the XREADGROUP block time, or every idle wait would count as a timeout."""
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=settings.worker_block_ms / 1000 + 5,
        socket_connect_timeout=settings.redis_connect_timeout_ms / 1000 + 2,
    )


class ClickConsumer:
    def __init__(
        self,
        redis: Redis,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        consumer_name: str | None = None,
        parse_ua: Callable[[str | None], UAInfo] = parse_user_agent,
    ) -> None:
        self.redis = redis
        self.sessionmaker = sessionmaker
        self.settings = settings
        # The pod name: stable for the life of a pod and unique among pods, so a restarted pod is
        # recognisable and its pending messages can be reclaimed (P3.3).
        self.name = consumer_name or socket.gethostname()
        self.parse_ua = parse_ua

    async def ensure_group(self) -> None:
        """Create the consumer group at `0` (and the stream if it does not exist yet).

        `0`, not `$`: events that were queued BEFORE the group existed (the API is deployed before
        the worker, or Redis was wiped) must be delivered, not skipped."""
        try:
            await self.redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):  # it already exists: fine
                raise

    async def read_batch(self) -> list[tuple[str, dict[str, str]]]:
        """New messages for this consumer (blocks up to WORKER_BLOCK_MS when there are none)."""
        response = await self.redis.xreadgroup(
            GROUP,
            self.name,
            {STREAM: ">"},
            count=self.settings.worker_batch_size,
            block=self.settings.worker_block_ms,
        )
        if not response:
            return []
        return [(msg_id, fields) for _, entries in response for msg_id, fields in entries]

    async def handle(self, entries: Sequence[tuple[str, Mapping[str, str]]]) -> BatchResult | None:
        """Process one batch and acknowledge it. Returns None for an empty batch."""
        if not entries:
            return None
        started = time.monotonic()
        messages: list[ClickMessage] = []
        for msg_id, fields in entries:
            try:
                messages.append(parse_message(msg_id, fields))
            except MalformedMessage as exc:
                # Left PENDING (never acknowledged). Dead-lettering is task P3.3.
                logger.error("click %s is malformed and stays pending (%s)", msg_id, exc)

        result = await process_batch(self.sessionmaker, messages, self.parse_ua)

        # Strictly AFTER the commit inside process_batch.
        if result.ack_ids:
            await self.redis.xack(STREAM, GROUP, *result.ack_ids)

        CLICKS_PROCESSED.labels(status="ok").inc(result.inserted)
        CLICKS_PROCESSED.labels(status="duplicate").inc(result.duplicates)
        CLICKS_PROCESSED.labels(status="orphan").inc(result.orphans)
        CLICK_BATCH_SECONDS.observe(time.monotonic() - started)
        return result

    async def run_once(self) -> BatchResult | None:
        return await self.handle(await self.read_batch())
