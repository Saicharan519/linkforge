"""Click analytics (PRD F4, ARCHITECTURE §3.1, §4). Routers stay thin and call these.

Two kinds of dates, never mixed (CLAUDE.md invariant):
- the DAILY BUCKETS are UTC calendar dates. `range=Nd` means the last N such days INCLUDING today,
  and today is partial: from `today - (N - 1)` to `today`, both inclusive. They are returned as
  plain "YYYY-MM-DD" strings and are never converted to another timezone;
- raw click timestamps (used for the breakdowns) are matched against the same window, as the
  half-open interval [start 00:00 UTC, end + 1 day 00:00 UTC).

Bots never count: the time series comes from `link_daily_stats` (which the worker only increments
for non-bots) and the breakdowns filter `is_bot = false`.

Every query is scoped to the caller: a link that belongs to someone else is a 404, like everywhere.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import ClickEvent, Link, LinkDailyStat
from app.schemas.analytics import (
    BrowserCount,
    DayCount,
    DeviceCount,
    LinkAnalytics,
    Overview,
    RangeName,
    ReferrerCount,
    TopLink,
)

TOP_REFERRERS = 10
TOP_BROWSERS = 10
TOP_LINKS = 5
DIRECT = "(direct)"  # D28: how an empty referrer is reported


def window(range_name: RangeName, now: datetime) -> tuple[date, date]:
    """(first day, last day) of the range, UTC, both inclusive. The last day is today."""
    days = int(range_name[:-1])
    today = now.astimezone(UTC).date()
    return today - timedelta(days=days - 1), today


def _bounds(first: date, last: date) -> tuple[datetime, datetime]:
    """The same window as a half-open interval of instants, for raw click timestamps."""
    start = datetime.combine(first, time.min, tzinfo=UTC)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=UTC)
    return start, end


def _zero_filled(first: date, last: date, counts: dict[date, int]) -> list[DayCount]:
    """One entry for EVERY day of the window, 0 where nothing happened (charts need no gaps)."""
    days = (last - first).days + 1
    return [
        DayCount(day=(d := first + timedelta(days=i)).isoformat(), clicks=counts.get(d, 0))
        for i in range(days)
    ]


async def link_analytics(
    db: AsyncSession, user_id: uuid.UUID, link_id: uuid.UUID, range_name: RangeName, now: datetime
) -> LinkAnalytics:
    owned = await db.execute(select(Link.id).where(Link.id == link_id, Link.user_id == user_id))
    if owned.scalar_one_or_none() is None:
        raise ApiError(404, "not_found", "link not found")

    first, last = window(range_name, now)
    start, end = _bounds(first, last)

    daily = await db.execute(
        select(LinkDailyStat.day, LinkDailyStat.clicks).where(
            LinkDailyStat.link_id == link_id, LinkDailyStat.day.between(first, last)
        )
    )
    counts = {day: clicks for day, clicks in daily}
    series = _zero_filled(first, last, counts)

    in_window = (
        ClickEvent.link_id == link_id,
        ClickEvent.is_bot.is_(False),
        ClickEvent.clicked_at >= start,
        ClickEvent.clicked_at < end,
    )

    host = func.coalesce(func.nullif(ClickEvent.referrer_host, ""), DIRECT)
    n = func.count()
    referrers = await db.execute(
        select(host.label("host"), n.label("clicks"))
        .where(*in_window)
        .group_by(host)
        .order_by(n.desc(), host)  # ties alphabetically, so the order is stable
        .limit(TOP_REFERRERS)
    )

    device = func.coalesce(ClickEvent.device_type, "other")
    devices = await db.execute(
        select(device.label("device_type"), n.label("clicks"))
        .where(*in_window)
        .group_by(device)
        .order_by(n.desc(), device)
    )

    browser = func.coalesce(ClickEvent.browser, "Other")
    browsers = await db.execute(
        select(browser.label("browser"), n.label("clicks"))
        .where(*in_window)
        .group_by(browser)
        .order_by(n.desc(), browser)
        .limit(TOP_BROWSERS)
    )

    return LinkAnalytics(
        total=sum(counts.values()),
        series=series,
        referrers=[ReferrerCount(host=h, clicks=c) for h, c in referrers],
        devices=[DeviceCount(device_type=d, clicks=c) for d, c in devices],  # type: ignore[arg-type]
        browsers=[BrowserCount(browser=b, clicks=c) for b, c in browsers],
    )


async def overview(
    db: AsyncSession, user_id: uuid.UUID, range_name: RangeName, now: datetime
) -> Overview:
    first, last = window(range_name, now)

    total_links = (
        await db.execute(select(func.count()).select_from(Link).where(Link.user_id == user_id))
    ).scalar_one()

    # Only the caller's links: the join to links carries the user_id filter.
    in_range = (Link.user_id == user_id, LinkDailyStat.day.between(first, last))
    per_day = await db.execute(
        select(LinkDailyStat.day, func.sum(LinkDailyStat.clicks))
        .join(Link, Link.id == LinkDailyStat.link_id)
        .where(*in_range)
        .group_by(LinkDailyStat.day)
    )
    counts = {day: int(total) for day, total in per_day}

    clicks = func.sum(LinkDailyStat.clicks)
    top = await db.execute(
        select(Link.id, Link.code, Link.title, clicks.label("clicks"))
        .join(LinkDailyStat, LinkDailyStat.link_id == Link.id)
        .where(*in_range)
        .group_by(Link.id)
        .order_by(clicks.desc(), Link.code)
        .limit(TOP_LINKS)
    )

    return Overview(
        total_links=total_links,
        total_clicks=sum(counts.values()),
        series=_zero_filled(first, last, counts),
        top_links=[
            TopLink(id=link_id, code=code, title=title, clicks=int(total))
            for link_id, code, title, total in top
        ],
    )
