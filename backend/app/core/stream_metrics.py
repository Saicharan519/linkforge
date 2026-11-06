"""The click stream gauges: how far behind is the worker? (ARCHITECTURE §7, §7.1)

  click_stream_length   XLEN: entries in the stream (acknowledged ones stay until trimmed)
  click_stream_lag      entries no worker has been handed yet (from XINFO GROUPS `lag`)
  click_stream_pending  entries handed out but not yet acknowledged (XINFO GROUPS `pending`)

Lag growing means no (or a too slow) worker; pending growing means workers receive messages but do
not finish them (poison messages, a stuck database). The alerts in §7.1 watch exactly these.

They are refreshed on a timer into plain Gauges (the metrics HTTP server runs in its own thread and
cannot await Redis) and are exported by BOTH the API and the worker. The API matters most: it keeps
running when the worker is down, which is precisely when these numbers must stay visible. Two
processes export the same numbers, so queries should use max(), not sum().

If Redis cannot answer, the gauges become NaN ("unknown"), which alert rules treat as "not
firing" instead of showing a stale or false value.
"""

import asyncio
import logging

from app.core.metrics import CLICK_STREAM_LAG, CLICK_STREAM_LENGTH, CLICK_STREAM_PENDING
from app.core.redis import SafeRedis
from app.services.clicks import STREAM

logger = logging.getLogger(__name__)

GROUP = "click-workers"  # kept in step with app.worker.consumer.GROUP (a test pins it)
UNKNOWN = float("nan")
_FAILED = object()


async def refresh_stream_gauges(redis: SafeRedis) -> None:
    exists = await redis.call("metrics", redis.client.exists(STREAM), _FAILED)
    if exists is _FAILED:
        _set(UNKNOWN, UNKNOWN, UNKNOWN)
        return
    if not exists:
        _set(0, 0, 0)  # no stream yet: nothing queued
        return

    length = await redis.call("metrics", redis.client.xlen(STREAM), _FAILED)
    groups = await redis.call("metrics", redis.client.xinfo_groups(STREAM), _FAILED)
    if length is _FAILED or groups is _FAILED:
        _set(UNKNOWN, UNKNOWN, UNKNOWN)
        return

    group = next((g for g in groups if g.get("name") == GROUP), None)  # type: ignore[union-attr]
    if group is None:
        # No consumer group yet (the worker never ran): everything is waiting.
        _set(length, length, 0)  # type: ignore[arg-type]
        return

    lag = group.get("lag")
    if lag is None:
        # Redis cannot say (it happens after entries were trimmed or deleted). XLEN is an UPPER
        # BOUND of the true lag, so the gauge may overstate the backlog but never hides one.
        lag = length
    _set(length, lag, group.get("pending", 0))  # type: ignore[arg-type]


def _set(length: float, lag: float, pending: float) -> None:
    CLICK_STREAM_LENGTH.set(length)
    CLICK_STREAM_LAG.set(lag)
    CLICK_STREAM_PENDING.set(pending)


async def run_gauge_refresher(redis: SafeRedis, interval_seconds: float) -> None:
    """Refresh forever (cancel the task to stop). One failed round never ends the loop."""
    while True:
        try:
            await refresh_stream_gauges(redis)
        except Exception:
            logger.exception("refreshing the stream gauges failed")
        await asyncio.sleep(interval_seconds)
