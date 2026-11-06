"""Analytics endpoints (task P3.5, PRD F4, ARCHITECTURE §3.1, decisions D27-D29).

Clicks are seeded through the REAL worker code (`process_batch`) so the time series and the
breakdowns come from the same rows the product creates. Real Postgres and Redis.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import create_engine, create_sessionmaker
from app.models import Link, User
from app.services.analytics import window
from app.worker.consumer import parse_message, process_batch
from tests.conftest import TEST_DATABASE_URL
from tests.integration.browser import ORIGIN, PASSWORD, PASSWORD_HASH, Browser
from tests.integration.conftest import ApiAs, running_app
from tests.integration.factories import make_link, make_user

# "Now" is 12:00 UTC on 2026-11-06. A 7-day range is therefore 2026-10-31 .. 2026-11-06.
NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
TODAY = date(2026, 11, 6)

CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
IPAD = (
    "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
CURL = "curl/8.5.0"


@pytest.fixture
async def sm() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(Settings(database_url=TEST_DATABASE_URL), pool_size=2, max_overflow=1)
    yield create_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
async def owner(db: AsyncSession) -> User:
    return await make_user(db, "owner@example.com", password_hash=PASSWORD_HASH)


@pytest.fixture
async def other(db: AsyncSession) -> User:
    return await make_user(db, "other@example.com", password_hash=PASSWORD_HASH)


async def clicks(
    sm: async_sessionmaker[AsyncSession],
    link: Link,
    *rows: tuple[datetime, str, str],
) -> None:
    """Store clicks for `link` as the worker would. Each row: (when, user agent, referrer host)."""
    messages = [
        parse_message(
            f"{i + 1}-0",
            {
                "event_id": str(uuid.uuid4()),
                "link_id": str(link.id),
                "code": link.code,
                "ts": when.isoformat(),
                "ip_hash": "h" * 64,
                "user_agent": ua,
                "referrer_host": referrer,
            },
        )
        for i, (when, ua, referrer) in enumerate(rows)
    ]
    await process_batch(sm, messages)


def at(day: date, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def days_ago(n: int) -> date:
    return TODAY - timedelta(days=n)


def by_day(series: list[dict[str, object]]) -> dict[str, int]:
    return {str(item["day"]): int(item["clicks"]) for item in series}  # type: ignore[call-overload]


# --- the window -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("range_name", "first"),
    [("7d", date(2026, 10, 31)), ("30d", date(2026, 10, 8)), ("90d", date(2026, 8, 9))],
)
def test_the_window_is_the_last_n_utc_days_including_today(range_name: str, first: date) -> None:
    assert window(range_name, NOW) == (first, TODAY)  # type: ignore[arg-type]


def test_the_window_uses_the_utc_date_not_the_local_one() -> None:
    # 01:30 on the 7th at UTC+2 is still the 6th in UTC: the buckets are UTC days.
    local = datetime(2026, 11, 7, 1, 30, tzinfo=timezone(timedelta(hours=2)))
    assert window("7d", local)[1] == date(2026, 11, 6)
    # and just after midnight UTC it is already the 7th
    assert window("7d", datetime(2026, 11, 7, 0, 0, 1, tzinfo=UTC))[1] == date(2026, 11, 7)


# --- per-link analytics -----------------------------------------------------------------------


async def test_series_is_zero_filled_for_every_day_with_utc_date_labels(
    api_as: ApiAs, owner: User, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    link = await make_link(db, owner, "series1")
    await clicks(
        sm, link, (at(TODAY), CHROME, ""), (at(TODAY), CHROME, ""), (at(days_ago(3)), CHROME, "")
    )
    async with api_as(owner, now=NOW) as (_, c):
        for name, length in (("7d", 7), ("30d", 30), ("90d", 90)):
            body = (await c.get(f"/api/v1/links/{link.id}/analytics?range={name}")).json()
            series = body["series"]
            assert len(series) == length  # one entry per day, no gaps
            assert series[-1]["day"] == "2026-11-06"  # today, last
            assert series[0]["day"] == (TODAY - timedelta(days=length - 1)).isoformat()
            assert all(isinstance(s["day"], str) and len(s["day"]) == 10 for s in series)
            counts = by_day(series)
            assert counts["2026-11-06"] == 2 and counts["2026-11-03"] == 1
            assert sum(counts.values()) == body["total"] == 3
            zero_days = [d for d, n in counts.items() if n == 0]
            assert len(zero_days) == length - 2


async def test_default_range_is_30_days(api_as: ApiAs, owner: User, db: AsyncSession) -> None:
    link = await make_link(db, owner, "dflt001")
    async with api_as(owner, now=NOW) as (_, c):
        body = (await c.get(f"/api/v1/links/{link.id}/analytics")).json()
    assert len(body["series"]) == 30
    assert body["total"] == 0 and body["referrers"] == [] and body["devices"] == []


async def test_a_link_with_no_clicks_has_empty_but_complete_data(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "empty01")
    async with api_as(owner, now=NOW) as (_, c):
        body = (await c.get(f"/api/v1/links/{link.id}/analytics?range=7d")).json()
    assert body["total"] == 0
    assert [s["clicks"] for s in body["series"]] == [0] * 7
    assert (body["referrers"], body["devices"], body["browsers"]) == ([], [], [])


@pytest.mark.parametrize(
    ("when", "inside"),
    [
        (datetime(2026, 10, 31, 0, 0, 0, tzinfo=UTC), True),  # exactly the first instant
        (datetime(2026, 10, 30, 23, 59, 59, 999999, tzinfo=UTC), False),  # a microsecond before
        (datetime(2026, 11, 6, 23, 59, 59, 999999, tzinfo=UTC), True),  # last instant of today
        (datetime(2026, 11, 7, 0, 0, 0, tzinfo=UTC), False),  # tomorrow
        (datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC), True),  # now: today is PARTIAL, still counts
        (
            datetime(2026, 10, 31, 1, 30, tzinfo=timezone(timedelta(hours=2))),
            False,
        ),  # = 10-30 23:30Z
    ],
    ids=["first-instant", "microsecond-before", "end-of-today", "tomorrow", "now", "offset-before"],
)
async def test_range_boundaries_apply_to_the_series_and_to_every_breakdown(
    api_as: ApiAs,
    owner: User,
    db: AsyncSession,
    sm: async_sessionmaker[AsyncSession],
    when: datetime,
    inside: bool,
) -> None:
    link = await make_link(db, owner, "bound01")
    await clicks(sm, link, (when, IPHONE, "edge.example"))
    async with api_as(owner, now=NOW) as (_, c):
        body = (await c.get(f"/api/v1/links/{link.id}/analytics?range=7d")).json()
    expected = 1 if inside else 0
    assert body["total"] == expected
    assert sum(s["clicks"] for s in body["series"]) == expected
    assert sum(r["clicks"] for r in body["referrers"]) == expected
    assert sum(d["clicks"] for d in body["devices"]) == expected
    assert sum(b["clicks"] for b in body["browsers"]) == expected


async def test_the_midnight_edge_puts_clicks_on_the_right_utc_day(
    api_as: ApiAs, owner: User, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    link = await make_link(db, owner, "midnite")
    await clicks(
        sm,
        link,
        (datetime(2026, 11, 5, 23, 59, 59, 999999, tzinfo=UTC), CHROME, ""),
        (datetime(2026, 11, 6, 0, 0, 0, tzinfo=UTC), CHROME, ""),
        (
            datetime(2026, 11, 6, 1, 30, tzinfo=timezone(timedelta(hours=2))),
            CHROME,
            "",
        ),  # 11-05 23:30Z
    )
    async with api_as(owner, now=NOW) as (_, c):
        counts = by_day(
            (await c.get(f"/api/v1/links/{link.id}/analytics?range=7d")).json()["series"]
        )
    assert (counts["2026-11-05"], counts["2026-11-06"]) == (2, 1)


async def test_older_clicks_are_in_a_longer_range_only(
    api_as: ApiAs, owner: User, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    link = await make_link(db, owner, "older01")
    await clicks(sm, link, (at(days_ago(40)), CHROME, ""), (at(days_ago(1)), CHROME, ""))
    async with api_as(owner, now=NOW) as (_, c):
        totals = {
            r: (await c.get(f"/api/v1/links/{link.id}/analytics?range={r}")).json()["total"]
            for r in ("7d", "30d", "90d")
        }
    assert totals == {"7d": 1, "30d": 1, "90d": 2}


async def test_bots_are_excluded_from_everything(
    api_as: ApiAs, owner: User, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    link = await make_link(db, owner, "bots001")
    await clicks(
        sm,
        link,
        (at(TODAY), CHROME, "news.example"),
        (at(TODAY), GOOGLEBOT, "bot.example"),
        (at(TODAY), CURL, ""),
        (at(TODAY), "", "scripts.example"),  # no user agent: a bot
    )
    async with api_as(owner, now=NOW) as (_, c):
        body = (await c.get(f"/api/v1/links/{link.id}/analytics?range=7d")).json()
        overview = (await c.get("/api/v1/analytics/overview?range=7d")).json()
    assert body["total"] == 1
    assert body["referrers"] == [{"host": "news.example", "clicks": 1}]
    assert body["devices"] == [{"device_type": "desktop", "clicks": 1}]
    assert body["browsers"] == [{"browser": "Chrome", "clicks": 1}]
    assert overview["total_clicks"] == 1
    assert [t["clicks"] for t in overview["top_links"]] == [1]


async def test_referrers_top_ten_direct_label_and_stable_order(
    api_as: ApiAs, owner: User, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    link = await make_link(db, owner, "refs001")
    rows = [(at(TODAY), CHROME, "")] * 3  # 3 direct visits
    rows += [(at(TODAY), CHROME, "big.example")] * 5
    rows += [(at(TODAY), CHROME, f"site{i:02d}.example") for i in range(12)]  # 12 hosts, 1 each
    await clicks(sm, link, *rows)
    async with api_as(owner, now=NOW) as (_, c):
        refs = (await c.get(f"/api/v1/links/{link.id}/analytics?range=7d")).json()["referrers"]
    assert len(refs) == 10  # capped
    assert refs[0] == {"host": "big.example", "clicks": 5}
    assert refs[1] == {"host": "(direct)", "clicks": 3}  # an empty referrer is "(direct)"
    # the rest are the 1-click hosts in alphabetical order, so the list is stable
    assert [r["host"] for r in refs[2:]] == [f"site{i:02d}.example" for i in range(8)]


async def test_devices_and_browsers_are_counted_by_group(
    api_as: ApiAs, owner: User, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    link = await make_link(db, owner, "devs001")
    await clicks(
        sm,
        link,
        *[(at(TODAY), CHROME, "")] * 3,
        *[(at(TODAY), IPHONE, "")] * 2,
        (at(TODAY), IPAD, ""),
        (at(TODAY), FIREFOX, ""),
    )
    async with api_as(owner, now=NOW) as (_, c):
        body = (await c.get(f"/api/v1/links/{link.id}/analytics?range=7d")).json()
    assert body["devices"] == [
        {"device_type": "desktop", "clicks": 4},
        {"device_type": "mobile", "clicks": 2},
        {"device_type": "tablet", "clicks": 1},
    ]  # only groups that have clicks, biggest first
    assert body["browsers"] == [
        {"browser": "Chrome", "clicks": 3},
        {"browser": "Safari", "clicks": 3},  # iPhone and iPad Safari are one browser
        {"browser": "Firefox", "clicks": 1},
    ]


async def test_browsers_are_capped_at_ten(
    api_as: ApiAs, owner: User, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    link = await make_link(db, owner, "brws001")
    uas = [f"Mozilla/5.0 (X11; Linux x86_64) Brand{i:02d}Browser/1.{i}" for i in range(12)]
    await clicks(sm, link, *[(at(TODAY), ua, "") for ua in uas])
    async with api_as(owner, now=NOW) as (_, c):
        body = (await c.get(f"/api/v1/links/{link.id}/analytics?range=7d")).json()
    assert len(body["browsers"]) <= 10


# --- ownership and validation -----------------------------------------------------------------


async def test_another_users_link_and_an_unknown_id_are_the_same_404(
    api_as: ApiAs, owner: User, other: User, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    theirs = await make_link(db, other, "theirs1")
    await clicks(sm, theirs, (at(TODAY), CHROME, ""))
    async with api_as(owner, now=NOW) as (_, c):
        foreign = await c.get(f"/api/v1/links/{theirs.id}/analytics")
        unknown = await c.get(f"/api/v1/links/{uuid.uuid4()}/analytics")
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()
    assert foreign.json()["error"]["code"] == "not_found"


async def test_a_deleted_links_analytics_are_gone(
    api_as: ApiAs, owner: User, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    link = await make_link(db, owner, "gone001")
    await clicks(sm, link, (at(TODAY), CHROME, ""))
    link_id = link.id
    async with api_as(owner, now=NOW) as (_, c):
        assert (await c.get(f"/api/v1/links/{link_id}/analytics")).status_code == 200
        await db.execute(delete(Link).where(Link.id == link_id))
        await db.commit()
        assert (await c.get(f"/api/v1/links/{link_id}/analytics")).status_code == 404


@pytest.mark.parametrize("bad", ["1d", "7", "", "30D", "365d", "abc", "-7d", "7d,30d"])
async def test_an_invalid_range_is_422_naming_the_field(
    api_as: ApiAs, owner: User, db: AsyncSession, bad: str
) -> None:
    link = await make_link(db, owner, "range01")
    async with api_as(owner, now=NOW) as (_, c):
        one = await c.get(f"/api/v1/links/{link.id}/analytics", params={"range": bad})
        two = await c.get("/api/v1/analytics/overview", params={"range": bad})
    for resp in (one, two):
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"
        assert "range" in {d["field"] for d in resp.json()["error"]["details"]}


async def test_a_malformed_link_id_is_422(api_as: ApiAs, owner: User) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.get("/api/v1/links/not-a-uuid/analytics")
    assert resp.status_code == 422


# --- overview ---------------------------------------------------------------------------------


async def test_overview_counts_only_my_links_and_my_clicks(
    api_as: ApiAs, owner: User, other: User, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    mine1 = await make_link(db, owner, "mine001")
    mine2 = await make_link(
        db, owner, "mine002", is_active=False
    )  # disabled still counts as a link
    await make_link(db, owner, "mine003", expires_at=NOW - timedelta(days=1))  # so does expired
    theirs = await make_link(db, other, "theirs2")
    await clicks(sm, mine1, (at(TODAY), CHROME, ""), (at(days_ago(2)), CHROME, ""))
    await clicks(sm, mine2, (at(days_ago(2)), CHROME, ""))
    await clicks(sm, theirs, *[(at(TODAY), CHROME, "")] * 50)
    async with api_as(owner, now=NOW) as (_, c):
        body = (await c.get("/api/v1/analytics/overview?range=7d")).json()
    assert body["total_links"] == 3
    assert body["total_clicks"] == 3  # nothing of the other user's 50
    counts = by_day(body["series"])
    assert len(body["series"]) == 7
    assert (counts["2026-11-06"], counts["2026-11-04"]) == (1, 2)
    assert sum(counts.values()) == body["total_clicks"]


async def test_overview_top_links_are_the_five_busiest_with_clicks_in_range(
    api_as: ApiAs, owner: User, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    busy = {}
    for i, n in enumerate([9, 7, 5, 5, 3, 2, 1]):
        link = await make_link(db, owner, f"top{i:04d}", title=f"Link {i}" if i % 2 == 0 else None)
        busy[link.code] = n
        await clicks(sm, link, *[(at(TODAY), CHROME, "")] * n)
    quiet = await make_link(db, owner, "quiet01")  # no clicks: never listed
    old = await make_link(db, owner, "old0001")
    await clicks(sm, old, *[(at(days_ago(60)), CHROME, "")] * 20)  # busy, but outside 7d
    async with api_as(owner, now=NOW) as (_, c):
        top = (await c.get("/api/v1/analytics/overview?range=7d")).json()["top_links"]
        wide = (await c.get("/api/v1/analytics/overview?range=90d")).json()["top_links"]
    assert [(t["code"], t["clicks"]) for t in top] == [
        ("top0000", 9),
        ("top0001", 7),
        ("top0002", 5),
        ("top0003", 5),
        ("top0004", 3),
    ]  # top 5; the 5-5 tie is broken by code
    assert top[0]["title"] == "Link 0" and top[1]["title"] is None
    assert quiet.code not in {t["code"] for t in top}
    assert wide[0] == {"id": str(old.id), "code": "old0001", "title": None, "clicks": 20}


async def test_overview_of_a_user_with_nothing(api_as: ApiAs, owner: User) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        body = (await c.get("/api/v1/analytics/overview")).json()
    assert (body["total_links"], body["total_clicks"], body["top_links"]) == (0, 0, [])
    assert len(body["series"]) == 30 and not any(s["clicks"] for s in body["series"])


# --- identity: session and API key ------------------------------------------------------------


async def test_analytics_need_authentication_and_accept_session_or_api_key(
    owner: User, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    link = await make_link(db, owner, "auth001")
    await clicks(sm, link, (at(TODAY), CHROME, ""))
    settings = Settings(database_url=TEST_DATABASE_URL, allowed_origins=ORIGIN)
    url = f"/api/v1/links/{link.id}/analytics?range=7d"
    async with running_app(settings, overrides=None) as (_, anonymous):
        for path in (url, "/api/v1/analytics/overview"):
            resp = await anonymous.get(path)
            assert resp.status_code == 401 and resp.json()["error"]["code"] == "unauthorized"

        browser = await Browser(anonymous).login("owner@example.com", PASSWORD)
        assert (await browser.get(url)).status_code == 200  # a session; a GET needs no CSRF token

        key = (await browser.post("/api/v1/api-keys", json={"name": "ci"})).json()["key"]
    async with running_app(settings) as (_, scripted):  # no cookies at all
        resp = await scripted.get(url, headers={"X-API-Key": key})
        assert resp.status_code == 200 and resp.json()["total"] in (0, 1)
        bad = await scripted.get(url, headers={"X-API-Key": "lf_ZZZZZZZZ_" + "a" * 43})
        assert bad.status_code == 401


async def test_the_series_total_matches_the_stored_clicks_after_redelivery(
    api_as: ApiAs, owner: User, db: AsyncSession, sm: async_sessionmaker[AsyncSession]
) -> None:
    """The same messages processed twice (at-least-once delivery) must not inflate analytics."""
    link = await make_link(db, owner, "redel02")
    batch = [
        parse_message(
            f"{i}-0",
            {
                "event_id": str(uuid.uuid4()),
                "link_id": str(link.id),
                "code": "x",
                "ts": at(TODAY).isoformat(),
                "ip_hash": "h" * 64,
                "user_agent": CHROME,
                "referrer_host": "r.example",
            },
        )
        for i in range(1, 4)
    ]
    await process_batch(sm, batch)
    await process_batch(sm, batch)  # redelivered
    async with api_as(owner, now=NOW) as (_, c):
        body = (await c.get(f"/api/v1/links/{link.id}/analytics?range=7d")).json()
    assert body["total"] == 3
    assert body["referrers"] == [{"host": "r.example", "clicks": 3}]  # events AND counters agree


async def _unused(c: AsyncClient) -> None:  # keeps the AsyncClient import used for typing readers
    return None
