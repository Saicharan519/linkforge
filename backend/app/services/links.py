"""Link business logic. Routers stay thin and call these functions.

INVARIANT: every query on `links` is scoped by user_id. Another user's link is reported as
"not found" (404), never "forbidden", so link ids cannot be probed.
"""

import uuid
from datetime import datetime

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import ApiError
from app.core.redis import SafeRedis
from app.core.shortcode import (
    InvalidAlias,
    is_code_collision,
    validate_alias,
    with_unique_code,
)
from app.core.urls import InvalidUrl, validate_destination_url
from app.models import Link, LinkDailyStat
from app.schemas.link import LinkCreate, LinkList, LinkOut, LinkPatch, LinkStatus
from app.services import link_cache

Problems = list[tuple[str, str]]  # (field, message) pairs for a 422


def link_status(is_active: bool, expires_at: datetime | None, now: datetime) -> LinkStatus:
    """The single definition of status. Disabled beats expired (PRD F2, ARCHITECTURE §2.1)."""
    if not is_active:
        return "disabled"
    if expires_at is not None and expires_at <= now:
        return "expired"
    return "active"


def _status_condition(status: LinkStatus, now: datetime):  # noqa: ANN202 - SQLAlchemy expression
    """The SQL twin of link_status(), so list filters and the status field cannot disagree."""
    if status == "disabled":
        return Link.is_active.is_(False)
    if status == "expired":
        return and_(Link.is_active.is_(True), Link.expires_at.is_not(None), Link.expires_at <= now)
    return and_(Link.is_active.is_(True), or_(Link.expires_at.is_(None), Link.expires_at > now))


def _to_out(link: Link, total_clicks: int, settings: Settings, now: datetime) -> LinkOut:
    return LinkOut(
        id=link.id,
        code=link.code,
        # Built server-side from SHORT_URL_BASE: clients never assemble short URLs themselves.
        short_url=f"{settings.short_url_base.rstrip('/')}/{link.code}",
        destination_url=link.destination_url,
        title=link.title,
        is_active=link.is_active,
        expires_at=link.expires_at,
        created_at=link.created_at,
        updated_at=link.updated_at,
        status=link_status(link.is_active, link.expires_at, now),
        total_clicks=total_clicks,
    )


async def _total_clicks(db: AsyncSession, link_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Clicks per link for a whole page of links in ONE query (never one query per link).

    The sum is over the daily rollups, which the worker only ever increments for real (non-bot)
    clicks, so bots are excluded by construction.
    """
    if not link_ids:
        return {}
    stmt = (
        select(LinkDailyStat.link_id, func.sum(LinkDailyStat.clicks))
        .where(LinkDailyStat.link_id.in_(link_ids))
        .group_by(LinkDailyStat.link_id)
    )
    return {link_id: int(total) for link_id, total in await db.execute(stmt)}


async def _get_owned(db: AsyncSession, user_id: uuid.UUID, link_id: uuid.UUID) -> Link:
    stmt = select(Link).where(Link.id == link_id, Link.user_id == user_id)
    link = (await db.execute(stmt)).scalar_one_or_none()
    if link is None:
        raise ApiError(404, "not_found", "link not found")
    return link


def _check_url(url: str, settings: Settings) -> Problems:
    try:
        validate_destination_url(url, short_url_base=settings.short_url_base)
    except InvalidUrl as exc:
        return [("destination_url", str(exc))]
    return []


def _check_expiry(expires_at: datetime | None, now: datetime) -> Problems:
    if expires_at is not None and expires_at <= now:
        return [("expires_at", "must be in the future")]
    return []


# --- create ---------------------------------------------------------------------------------


async def create_link(
    db: AsyncSession,
    redis: SafeRedis,
    user_id: uuid.UUID,
    body: LinkCreate,
    settings: Settings,
    now: datetime,
) -> LinkOut:
    problems = _check_url(body.destination_url, settings) + _check_expiry(body.expires_at, now)
    if body.alias is not None:
        try:
            validate_alias(body.alias)
        except InvalidAlias as exc:
            problems.append(("alias", str(exc)))
    if problems:
        raise ApiError.validation(problems)

    async def insert(code: str) -> Link:
        link = Link(
            user_id=user_id,
            code=code,
            destination_url=body.destination_url,
            title=body.title,
            expires_at=body.expires_at,
        )
        # A savepoint: if the INSERT violates a constraint only this attempt is rolled back and
        # the session stays usable for the next attempt.
        async with db.begin_nested():
            db.add(link)
            await db.flush()
        return link

    if body.alias is not None:
        try:
            link = await insert(body.alias)
        except IntegrityError as exc:
            if is_code_collision(exc):
                raise ApiError(409, "alias_taken", "this alias is already in use") from None
            raise
    else:
        link = await with_unique_code(insert)

    await db.commit()
    await db.refresh(link)
    # After the commit: a visitor may have asked for this code a moment ago and left a "does not
    # exist" entry (neg:{code}) that would keep answering 404 for up to a minute.
    await link_cache.invalidate(redis, link.code)
    return _to_out(link, 0, settings, now)


# --- read -----------------------------------------------------------------------------------


def _like_pattern(q: str) -> str:
    """Escape LIKE wildcards so a search for "50%" matches the text "50%", not "50<anything>"."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def list_links(
    db: AsyncSession,
    user_id: uuid.UUID,
    settings: Settings,
    now: datetime,
    *,
    q: str | None,
    status: LinkStatus | None,
    page: int,
    page_size: int,
) -> LinkList:
    conditions = [Link.user_id == user_id]
    if q and q.strip():
        pattern = _like_pattern(q.strip())
        conditions.append(
            or_(
                Link.code.ilike(pattern, escape="\\"),
                Link.title.ilike(pattern, escape="\\"),
                Link.destination_url.ilike(pattern, escape="\\"),
            )
        )
    if status is not None:
        conditions.append(_status_condition(status, now))

    total = (
        await db.execute(select(func.count()).select_from(Link).where(*conditions))
    ).scalar_one()
    stmt = (
        select(Link)
        .where(*conditions)
        # id as a tie-breaker keeps pages stable when several links share a created_at.
        .order_by(Link.created_at.desc(), Link.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    links = list((await db.execute(stmt)).scalars())
    totals = await _total_clicks(db, [link.id for link in links])
    return LinkList(
        items=[_to_out(link, totals.get(link.id, 0), settings, now) for link in links],
        total=total,
        page=page,
        page_size=page_size,
    )


async def get_link(
    db: AsyncSession, user_id: uuid.UUID, link_id: uuid.UUID, settings: Settings, now: datetime
) -> LinkOut:
    link = await _get_owned(db, user_id, link_id)
    totals = await _total_clicks(db, [link.id])
    return _to_out(link, totals.get(link.id, 0), settings, now)


# --- update ---------------------------------------------------------------------------------


async def update_link(
    db: AsyncSession,
    redis: SafeRedis,
    user_id: uuid.UUID,
    link_id: uuid.UUID,
    body: LinkPatch,
    settings: Settings,
    now: datetime,
) -> LinkOut:
    sent = body.model_fields_set  # exactly the fields present in the JSON body
    problems: Problems = []
    for field in ("destination_url", "is_active"):
        if field in sent and getattr(body, field) is None:
            problems.append((field, "may not be null"))
    if "destination_url" in sent and body.destination_url is not None:
        problems += _check_url(body.destination_url, settings)
    if "expires_at" in sent:  # null is allowed here (it clears the expiry)
        problems += _check_expiry(body.expires_at, now)
    if problems:
        raise ApiError.validation(problems)

    link = await _get_owned(db, user_id, link_id)
    if "destination_url" in sent:
        link.destination_url = body.destination_url  # type: ignore[assignment]  # not None: checked
    if "is_active" in sent:
        link.is_active = body.is_active  # type: ignore[assignment]  # not None: checked above
    if "title" in sent:
        link.title = body.title  # None clears it
    if "expires_at" in sent:
        link.expires_at = body.expires_at  # None clears it
    link.updated_at = now  # no DB trigger: the service sets it on every PATCH (D34)
    await db.commit()
    # After the commit (never before, or a reader could refill the cache from the old row).
    # Any PATCH invalidates: destination, is_active and expires_at all change what a redirect does.
    await link_cache.invalidate(redis, link.code)

    totals = await _total_clicks(db, [link.id])
    return _to_out(link, totals.get(link.id, 0), settings, now)


# --- delete ---------------------------------------------------------------------------------


async def delete_link(
    db: AsyncSession, redis: SafeRedis, user_id: uuid.UUID, link_id: uuid.UUID
) -> None:
    # RETURNING gives us the code, which the cache keys are named after.
    result = await db.execute(
        delete(Link).where(Link.id == link_id, Link.user_id == user_id).returning(Link.code)
    )
    code = result.scalar_one_or_none()
    if code is None:
        raise ApiError(404, "not_found", "link not found")
    await db.commit()  # click_events and link_daily_stats go with it (ON DELETE CASCADE)
    await link_cache.invalidate(redis, code)
