"""Resolve a short code to its destination: the heart of the product.

P1.5 reads Postgres only. Redis caching, the negative cache, rate limiting and click events are
layered on in P2/P3 following docs/ARCHITECTURE.md §2.1; this function is where they plug in.
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.shortcode import ALIAS_PATTERN
from app.models import Link
from app.services.links import link_status

logger = logging.getLogger(__name__)

# Anything that means "Postgres is unavailable": a refused or dropped connection, a timeout, or a
# driver error. All of them must become a clean 503, never a 500 (CLAUDE.md invariant).
_DB_UNAVAILABLE = (SQLAlchemyError, OSError, TimeoutError)


async def resolve_destination(db: AsyncSession, code: str, now: datetime) -> str:
    """Return the destination URL for `code`, or raise ApiError 404 / 410 / 503.

    Status precedence (ARCHITECTURE §2.1): disabled -> 404 (even if also expired), else
    expired -> 410, else redirect. An unknown code and a disabled link look identical (404), so
    visitors cannot tell that a disabled link ever existed.
    """
    # A code that could never have been created cannot exist. Answering 404 here, without asking
    # the database, keeps scanners and junk URLs (/favicon.ico, /.env, /a%00b) off Postgres and
    # keeps odd bytes from ever reaching a query.
    if not ALIAS_PATTERN.fullmatch(code):
        raise ApiError(404, "not_found", "link not found")

    try:
        stmt = select(Link.destination_url, Link.is_active, Link.expires_at).where(
            Link.code == code  # exact, case-sensitive: "Abc" and "abc" are different links
        )
        row = (await db.execute(stmt)).first()
    except _DB_UNAVAILABLE as exc:
        # Log only the exception type: connection errors can carry host/user details.
        logger.warning("redirect lookup failed: postgres unavailable (%s)", type(exc).__name__)
        raise ApiError(503, "unavailable", "the service is temporarily unavailable") from None

    if row is None:
        raise ApiError(404, "not_found", "link not found")
    status = link_status(row.is_active, row.expires_at, now)
    if status == "disabled":
        raise ApiError(404, "not_found", "link not found")
    if status == "expired":
        raise ApiError(410, "gone", "this link has expired")
    return row.destination_url
