"""The redirect cache: what is stored in Redis and how long it may live (ARCHITECTURE §2.1, §5).

Two keys per short code:
- `link:{code}`  JSON {"id", "url"} of an ACTIVE, non-expired link. Holds the link id so a cache
                 hit can still produce a click event (P3) without asking Postgres.
- `neg:{code}`   "1" for a code that does not exist or is disabled, so scanners and dead links do
                 not hit Postgres on every request.

Every operation goes through SafeRedis, so Redis trouble never raises: reads fall back to "not
cached", writes are skipped (and counted in redis_errors_total).

The cache is cache-aside with delete-on-write. That is NOT race-free (§2.1.1): a reader can fetch
the old row, a writer can update and DEL, and the reader can then write the OLD value back. We
accept that and bound it: the TTL is at most LINK_CACHE_TTL_SECONDS and never beyond the link's
expiry, so the worst case is a stale redirect for that long, not forever.
"""

import json
import logging
import math
import random
import uuid
from datetime import datetime

from app.config import Settings
from app.core.redis import SafeRedis

logger = logging.getLogger(__name__)

# Negative entries are short on purpose: a brand-new link must not look "not found" for long.
NEG_TTL_SECONDS = 60

# Returned instead of a cached value when Redis itself failed, so callers can tell "not cached"
# (None) from "Redis is unhealthy" and skip the remaining Redis steps of this request.
REDIS_FAILED = object()


def link_key(code: str) -> str:
    return f"link:{code}"


def neg_key(code: str) -> str:
    return f"neg:{code}"


def cache_ttl_seconds(settings: Settings, expires_at: datetime | None, now: datetime) -> int | None:
    """Seconds this link may stay cached, or None if it should not be cached at all.

    +-10% jitter spreads out expiry so many popular links do not all miss at the same instant.
    The jitter never raises the value above the configured maximum (the 300 s invariant), and the
    result is rounded DOWN so the entry can never outlive the link's expiry.
    """
    ttl = min(
        settings.link_cache_ttl_seconds * random.uniform(0.9, 1.1), settings.link_cache_ttl_seconds
    )
    if expires_at is not None:
        remaining = (expires_at - now).total_seconds()
        ttl = min(ttl, remaining)
        if math.floor(ttl) < 1:
            return None  # about to expire: caching it could only serve it past its expiry
    return max(1, math.floor(ttl))


async def get_cached_link(redis: SafeRedis, code: str) -> dict[str, str] | None | object:
    """The cached link, None if absent or unreadable, or REDIS_FAILED if Redis failed."""
    raw = await redis.call("cache_get", redis.client.get(link_key(code)), REDIS_FAILED)
    if raw is REDIS_FAILED or raw is None:
        return raw
    try:
        value = json.loads(raw)  # type: ignore[arg-type]  # decode_responses=True gives str
        return {"id": str(uuid.UUID(value["id"])), "url": str(value["url"])}
    except (ValueError, KeyError, TypeError):
        # Not something we wrote (or a format change): treat as a miss; the lookup overwrites it.
        logger.warning("redirect cache: unreadable entry ignored")
        return None


async def is_negative_cached(redis: SafeRedis, code: str) -> bool | object:
    """True if `code` is negatively cached, False if not, REDIS_FAILED if Redis failed."""
    raw = await redis.call("neg_get", redis.client.get(neg_key(code)), REDIS_FAILED)
    if raw is REDIS_FAILED:
        return REDIS_FAILED
    return raw is not None


async def cache_link(
    redis: SafeRedis,
    settings: Settings,
    code: str,
    link_id: uuid.UUID,
    destination_url: str,
    expires_at: datetime | None,
    now: datetime,
) -> None:
    ttl = cache_ttl_seconds(settings, expires_at, now)
    if ttl is None:
        return
    payload = json.dumps({"id": str(link_id), "url": destination_url})
    await redis.call("cache_set", redis.client.set(link_key(code), payload, ex=ttl), None)


async def cache_negative(redis: SafeRedis, code: str) -> None:
    await redis.call("neg_set", redis.client.set(neg_key(code), "1", ex=NEG_TTL_SECONDS), None)


async def invalidate(redis: SafeRedis, code: str) -> None:
    """Delete both keys for `code`. Call AFTER the database commit, never before.

    Before the commit, a concurrent reader could refill the cache from the still-old row.
    If this DEL fails the old value lives on until its TTL: bounded, counted, accepted.
    """
    await redis.call("cache_del", redis.client.delete(link_key(code), neg_key(code)), None)
