"""The click consumer (ARCHITECTURE §2.3).

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

When things go wrong (task P3.3):
  - a batch fails -> each message is retried ALONE, so one bad event cannot block 99 good ones;
  - a message that keeps failing stays PENDING, is reclaimed (XAUTOCLAIM) when it has been idle
    long enough, and after MAX_DELIVERIES deliveries it is moved to the dead-letter stream;
  - the database being unreachable is not the message's fault: nothing is retried one by one, the
    batch just stays pending;
  - the stream or group disappearing (Redis wiped) is repaired by recreating the group;
  - SIGTERM finishes the current batch, acknowledges it, and exits.
"""

import asyncio
import logging
import socket
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.metrics import CLICK_BATCH_SECONDS, CLICKS_PROCESSED
from app.models import ClickEvent, Link, LinkDailyStat
from app.services.clicks import STREAM
from app.worker.ua import UAInfo, parse_user_agent

logger = logging.getLogger(__name__)

GROUP = "click-workers"
DEAD_STREAM = "clicks:dead"

# One reclaim tick walks at most this many pages of idle messages, so a huge backlog of stuck
# messages cannot keep the worker from reading new ones.
MAX_RECLAIM_PAGES = 10
ERROR_PAUSE_SECONDS = 1.0


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
    dead: int = 0  # moved to the dead-letter stream


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


def is_fk_violation(exc: BaseException) -> bool:
    """Postgres refused the insert because the link row is gone (SQLSTATE 23503): the link was
    deleted between our orphan check and the insert. Not an error: the click is an orphan."""
    if not isinstance(exc, IntegrityError):
        return False
    orig = exc.orig
    return getattr(orig, "sqlstate", None) == "23503" or getattr(orig, "pgcode", None) == "23503"


def is_connectivity_error(exc: BaseException) -> bool:
    """The database could not be reached. The MESSAGES are not at fault, so retrying them one by
    one would only repeat the same timeout a hundred times."""
    if isinstance(exc, OSError | TimeoutError | InterfaceError):
        return True
    return isinstance(exc, DBAPIError) and exc.connection_invalidated


def describe(exc: BaseException) -> str:
    """A short, safe description for logs and the dead-letter record. The SQLAlchemy message
    includes the statement and its PARAMETERS, so only the driver's own message is used."""
    inner = getattr(exc, "orig", exc)
    return f"{type(exc).__name__}: {inner}"[:300]


async def process_batch(
    sessionmaker: async_sessionmaker[AsyncSession],
    messages: Sequence[ClickMessage],
    parse_ua: Callable[[str | None], UAInfo] = parse_user_agent,
    *,
    _after_prefilter: Callable[[], Awaitable[None]] | None = None,
) -> BatchResult:
    """Store a batch in ONE transaction (steps 1-4 of the module docstring). Does NOT touch Redis:
    the caller acknowledges the returned ids after this function has returned, i.e. after COMMIT.

    `_after_prefilter` is a test hook that runs between the orphan check and the insert, the exact
    window in which a link can be deleted.
    """
    if not messages:
        return BatchResult(0, 0, 0, [])

    async with sessionmaker() as session, session.begin():
        # 1. Orphan pre-filter: links deleted between the click and now.
        wanted = {m.link_id for m in messages}
        found = await session.execute(select(Link.id).where(Link.id.in_(wanted)))
        existing = set(found.scalars())
        live = [m for m in messages if m.link_id in existing]
        if _after_prefilter is not None:
            await _after_prefilter()

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
        # recognisable and its pending messages can be reclaimed.
        self.name = consumer_name or socket.gethostname()
        self.parse_ua = parse_ua

    # --- the group ----------------------------------------------------------------------------

    async def ensure_group(self) -> None:
        """Create the consumer group at `0` (and the stream if it does not exist yet).

        `0`, not `$`: events that were queued BEFORE the group existed (the API is deployed before
        the worker, or Redis was wiped) must be delivered, not skipped."""
        try:
            await self.redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):  # it already exists: fine
                raise

    # --- reading ------------------------------------------------------------------------------

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

    # --- storing ------------------------------------------------------------------------------

    async def handle(self, entries: Sequence[tuple[str, Mapping[str, str]]]) -> BatchResult | None:
        """Process one batch and acknowledge what is finished. None for an empty batch."""
        if not entries:
            return None
        started = time.monotonic()

        messages: list[ClickMessage] = []
        dead = 0
        for msg_id, fields in entries:
            try:
                messages.append(parse_message(msg_id, fields))
            except MalformedMessage as exc:
                # Retrying can never fix a message that is missing fields: straight to the dead
                # letters (and only acknowledged once the dead-letter write has succeeded).
                if await self._dead_letter(msg_id, fields, f"malformed: {exc}"):
                    dead += 1

        result = await self._store(messages)
        await self._finish(result)
        CLICK_BATCH_SECONDS.observe(time.monotonic() - started)
        return BatchResult(
            result.inserted, result.duplicates, result.orphans, result.ack_ids, dead=dead
        )

    async def _store(self, messages: Sequence[ClickMessage]) -> BatchResult:
        """The whole batch in one transaction; if that fails, each message on its own."""
        try:
            return await process_batch(self.sessionmaker, messages, self.parse_ua)
        except Exception as exc:
            if is_connectivity_error(exc):
                raise  # not the messages' fault: leave them all pending
            logger.warning(
                "batch of %d failed (%s); retrying each message", len(messages), describe(exc)
            )
            return await self._store_each(messages)

    async def _store_each(self, messages: Sequence[ClickMessage]) -> BatchResult:
        """Fallback: one transaction per message. A message that fails stays PENDING (it is
        redelivered later and, if it never succeeds, dead-lettered); the others go through."""
        inserted = duplicates = orphans = 0
        ack_ids: list[str] = []
        for m in messages:
            try:
                r = await process_batch(self.sessionmaker, [m], self.parse_ua)
            except Exception as exc:
                if is_fk_violation(exc):
                    # The link vanished between the orphan check and the insert: an orphan.
                    orphans += 1
                    ack_ids.append(m.msg_id)
                    continue
                if is_connectivity_error(exc):
                    # Finish what was stored so far (it is committed: it must be acknowledged),
                    # then stop; the rest stays pending.
                    await self._finish(BatchResult(inserted, duplicates, orphans, ack_ids))
                    raise
                logger.error("click %s failed and stays pending (%s)", m.msg_id, describe(exc))
                continue
            inserted += r.inserted
            duplicates += r.duplicates
            orphans += r.orphans
            ack_ids.extend(r.ack_ids)
        return BatchResult(inserted, duplicates, orphans, ack_ids)

    async def _finish(self, result: BatchResult) -> None:
        """Acknowledge (strictly AFTER the commit that happened in process_batch) and count."""
        await self._ack(result.ack_ids)
        CLICKS_PROCESSED.labels(status="ok").inc(result.inserted)
        CLICKS_PROCESSED.labels(status="duplicate").inc(result.duplicates)
        CLICKS_PROCESSED.labels(status="orphan").inc(result.orphans)

    async def _ack(self, ids: Sequence[str]) -> None:
        if not ids:
            return
        try:
            await self.redis.xack(STREAM, GROUP, *ids)
        except ResponseError as exc:
            # NOGROUP: Redis was wiped after the commit. The data is safe in Postgres and there is
            # nothing left to acknowledge in a group that no longer exists.
            if "NOGROUP" not in str(exc):
                raise
            logger.warning("ack skipped: the consumer group no longer exists")

    # --- giving up on a message ---------------------------------------------------------------

    async def _dead_letter(self, msg_id: str, fields: Mapping[str, str], error: str) -> bool:
        """Move a message to `clicks:dead`. The original is acknowledged ONLY IF that write
        succeeded: if it fails, the message stays pending and is tried again, so a failing
        dead-letter stream can never turn into silent loss."""
        record = {
            **fields,
            "original_id": msg_id,
            "error": error[:500],
            "failed_at": datetime.now(UTC).isoformat(),
        }
        try:
            await self.redis.xadd(
                DEAD_STREAM, record, maxlen=self.settings.click_dead_maxlen, approximate=True
            )  # type: ignore[arg-type]
        except RedisError as exc:
            logger.error(
                "dead-letter write for %s failed (%s); it stays pending", msg_id, type(exc).__name__
            )
            return False
        await self._ack([msg_id])
        CLICKS_PROCESSED.labels(status="dead").inc()
        logger.error("click %s moved to %s (%s)", msg_id, DEAD_STREAM, error[:120])
        return True

    # --- reclaiming ---------------------------------------------------------------------------

    async def reclaim(self) -> int:
        """Take over messages that have been pending too long (a crashed worker's, or ones that
        failed before) and process them. Returns how many were claimed."""
        claimed_total = 0
        cursor = "0-0"
        for _ in range(MAX_RECLAIM_PAGES):
            page = await self.redis.xautoclaim(
                STREAM,
                GROUP,
                self.name,
                min_idle_time=self.settings.worker_claim_idle_ms,
                start_id=cursor,
                count=self.settings.worker_batch_size,
            )
            next_cursor, claimed = page[0], page[1]
            if claimed:
                claimed_total += len(claimed)
                await self._handle_claimed(claimed)
            if next_cursor == "0-0":
                break
            cursor = next_cursor
        return claimed_total

    async def _handle_claimed(self, claimed: Sequence[tuple[str, Mapping[str, str]]]) -> None:
        # XAUTOCLAIM does not report how often a message was delivered; the extended XPENDING
        # does. (Claiming just raised the count by one.)
        pending = await self.redis.xpending_range(
            STREAM,
            GROUP,
            min=claimed[0][0],
            max=claimed[-1][0],
            count=max(100, len(claimed) * 10),
            consumername=self.name,
        )
        deliveries = {p["message_id"]: p["times_delivered"] for p in pending}

        retry: list[tuple[str, Mapping[str, str]]] = []
        for msg_id, fields in claimed:
            count = deliveries.get(msg_id, 0)
            if count >= self.settings.max_deliveries:
                await self._dead_letter(
                    msg_id, fields, f"gave up after {count} deliveries without success"
                )
            else:
                retry.append((msg_id, fields))
        await self.handle(retry)

    # --- the loop -----------------------------------------------------------------------------

    async def run_once(self) -> BatchResult | None:
        return await self.handle(await self.read_batch())

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Work until `stop` is set. The batch in progress always finishes (and is acknowledged)
        first, so a SIGTERM never leaves a half-done batch behind."""
        await self.ensure_group()
        last_reclaim = float("-inf")  # reclaim right at start: leftovers from before a restart
        while not stop.is_set():
            try:
                if time.monotonic() - last_reclaim >= self.settings.worker_claim_interval_seconds:
                    last_reclaim = time.monotonic()
                    await self.reclaim()
                await self.run_once()
            except ResponseError as exc:
                if "NOGROUP" not in str(exc):
                    logger.exception("redis error; retrying")
                    await self._pause(stop)
                    continue
                # Redis was wiped (stream or group gone): recreate the group, at 0, and carry on.
                logger.warning("consumer group missing; recreating it")
                await self.ensure_group()
            except Exception:
                # One bad round must not kill the worker. The batch is still pending, so it will
                # be delivered again.
                logger.exception("round failed; it will be retried")
                await self._pause(stop)

    @staticmethod
    async def _pause(stop: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=ERROR_PAUSE_SECONDS)
        except TimeoutError:
            pass
