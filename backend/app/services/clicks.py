"""Click events: building one and putting it on the Redis stream (ARCHITECTURE §2.2, §2.1 step 6).

Every successful `GET /{code}` queues one event; a separate worker (P3.2) stores them in Postgres.
Queueing is BEST EFFORT: if Redis cannot take the event, the visitor is redirected anyway and the
loss is counted in `click_events_dropped_total`. So click counts are a lower bound during Redis
outages (PRD F4), and this module never raises into the request.

Privacy: the raw client IP is never written anywhere. The stream carries only
HMAC-SHA256(IP_HASH_SECRET, ip): stable enough to count unique visitors, useless without the
secret to recover an address.
"""

import hashlib
import hmac
import logging
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from app.config import Settings
from app.core.metrics import CLICKS_DROPPED, CLICKS_ENQUEUED
from app.core.redis import SafeRedis

logger = logging.getLogger(__name__)

STREAM = "clicks"

USER_AGENT_MAX = 512  # ARCHITECTURE §2.2
REFERRER_HOST_MAX = 255  # a DNS name cannot be longer

_FAILED = object()


def hash_ip(ip: str, secret: str) -> str:
    """HMAC-SHA256 of the client address (hex). Keyed, so it cannot be reversed by hashing every
    possible IPv4 address, which a plain SHA-256 of an IP would allow."""
    return hmac.new(secret.encode(), ip.encode(), hashlib.sha256).hexdigest()


def referrer_host(referer: str | None) -> str:
    """The host of the Referer header only, lowercased, or "" (no header, not a URL, no host).

    The path and query string can contain private data (session ids, search terms), so they are
    dropped at the door and never stored. Userinfo and port are dropped too (`hostname`).
    """
    if not referer:
        return ""
    try:
        host = urlsplit(referer.strip()).hostname
    except ValueError:  # e.g. an unbalanced "[" in the authority part
        return ""
    return (host or "")[:REFERRER_HOST_MAX]


def build_event(
    *,
    settings: Settings,
    link_id: uuid.UUID,
    code: str,
    ip: str,
    user_agent: str | None,
    referer: str | None,
    now: datetime,
) -> dict[str, str]:
    """The stream message: flat string fields, exactly those of ARCHITECTURE §2.2."""
    return {
        "event_id": str(uuid.uuid4()),  # the worker's idempotency key (unique in Postgres)
        "link_id": str(link_id),
        "code": code,
        "ts": now.isoformat(),
        "ip_hash": hash_ip(ip, settings.ip_hash_secret),
        "user_agent": (user_agent or "")[:USER_AGENT_MAX],
        "referrer_host": referrer_host(referer),
    }


async def enqueue_click(redis: SafeRedis, settings: Settings, event: dict[str, str]) -> None:
    """XADD the event. Never raises; a failure is counted as a dropped click."""
    # MAXLEN ~ N: approximate trimming (cheap) keeps the stream a memory guard, not a promise
    # (§2.3.1). If the worker is down so long that the backlog passes the cap, the OLDEST events
    # are lost; that is the documented, bounded-loss design.
    added = await redis.call(
        "enqueue",
        redis.client.xadd(STREAM, event, maxlen=settings.click_stream_maxlen, approximate=True),  # type: ignore[arg-type]
        _FAILED,
    )
    if added is _FAILED:
        CLICKS_DROPPED.inc()
    else:
        CLICKS_ENQUEUED.inc()


def count_dropped() -> None:
    """For a click we did not even attempt to queue because Redis had already failed on this
    request: it is still a lost click and must show up in the same counter."""
    CLICKS_DROPPED.inc()
