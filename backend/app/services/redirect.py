"""Resolve a short code to its destination: the heart of the product.

Cache-aside on top of Postgres, following docs/ARCHITECTURE.md §2.1 step by step. Rate limiting
(P2.3) and click events (P3) plug in around this function.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import ApiError
from app.core.metrics import REDIRECT_CACHE
from app.core.redis import SafeRedis
from app.core.shortcode import ALIAS_PATTERN
from app.models import Link
from app.services import link_cache
from app.services.links import link_status

logger = logging.getLogger(__name__)

# Anything that means "Postgres is unavailable": a refused or dropped connection, a timeout, or a
# driver error. All of them must become a clean 503, never a 500 (CLAUDE.md invariant).
_DB_UNAVAILABLE = (SQLAlchemyError, OSError, TimeoutError)


@dataclass(frozen=True, slots=True)
class Resolved:
    """What a redirect needs: where to go, and which link it was (for the click event, P3)."""

    link_id: UUID
    destination_url: str


async def resolve_destination(
    db: AsyncSession,
    redis: SafeRedis,
    settings: Settings,
    code: str,
    now: datetime,
    *,
    redis_ok: bool = True,
) -> Resolved:
    """Return the link for `code`, or raise ApiError 404 / 410 / 503.

    Status precedence (ARCHITECTURE §2.1): disabled -> 404 (even if also expired), else
    expired -> 410, else redirect. An unknown code and a disabled link look identical (404), so
    visitors cannot tell that a disabled link ever existed.

     means the caller already saw Redis fail on this request (the rate limiter),
    so every Redis step is skipped and the answer comes from Postgres alone.
    """
    # A code that could never have been created cannot exist. Answering 404 here, without asking
    # Redis or the database, keeps scanners and junk URLs (/favicon.ico, /.env, /a%00b) out of
    # both and keeps odd bytes from ever reaching a query.
    if not ALIAS_PATTERN.fullmatch(code):
        raise ApiError(404, "not_found", "link not found")

    # Step 2: the link cache. A hit is answered without touching Postgres (so a Postgres outage
    # does not break links that are already cached: the accepted trade-off in §2.1).
    cached = await link_cache.get_cached_link(redis, code) if redis_ok else link_cache.REDIS_FAILED
    redis_healthy = cached is not link_cache.REDIS_FAILED
    if isinstance(cached, dict):
        REDIRECT_CACHE.labels(result="hit").inc()
        return Resolved(link_id=UUID(cached["id"]), destination_url=cached["url"])

    # Step 3: the negative cache. Skipped when step 2 already showed Redis is unhealthy: the next
    # calls would just spend another timeout each, and every Redis step has a fallback anyway
    # (one slow Redis must cost a request one timeout, not three).
    if redis_healthy:
        negative = await link_cache.is_negative_cached(redis, code)
        if negative is link_cache.REDIS_FAILED:
            redis_healthy = False
        elif negative:
            REDIRECT_CACHE.labels(result="negative").inc()
            raise ApiError(404, "not_found", "link not found")

    # Step 4: Postgres.
    REDIRECT_CACHE.labels(result="miss").inc()
    try:
        stmt = select(Link.id, Link.destination_url, Link.is_active, Link.expires_at).where(
            Link.code == code  # exact, case-sensitive: "Abc" and "abc" are different links
        )
        row = (await db.execute(stmt)).first()
    except _DB_UNAVAILABLE as exc:
        # Log only the exception type: connection errors can carry host/user details.
        logger.warning("redirect lookup failed: postgres unavailable (%s)", type(exc).__name__)
        raise ApiError(503, "unavailable", "the service is temporarily unavailable") from None

    status = None if row is None else link_status(row.is_active, row.expires_at, now)
    if row is None or status == "disabled":
        if redis_healthy:
            await link_cache.cache_negative(redis, code)
        raise ApiError(404, "not_found", "link not found")
    if status == "expired":
        # Not cached: a 410 is rare and must flip the moment the link is edited.
        raise ApiError(410, "gone", "this link has expired")

    # Step 5: remember it, for no longer than the link itself lives.
    if redis_healthy:
        await link_cache.cache_link(
            redis, settings, code, row.id, row.destination_url, row.expires_at, now
        )
    return Resolved(link_id=row.id, destination_url=row.destination_url)
