"""GET|HEAD /{code} (PRD F3, Postgres-only version). Real app, real Postgres."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_now
from app.config import Settings
from app.models import User
from tests.integration.conftest import ApiAs, running_app
from tests.integration.factories import make_link, make_user

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
DEST = "https://example.com/landing?utm=1#top"
DEAD_DB = "postgresql+asyncpg://nobody:nopass@127.0.0.1:1/none"  # nothing listens on port 1


@pytest.fixture
async def owner(db: AsyncSession) -> User:
    return await make_user(db, "owner@example.com")


@pytest.fixture
async def at_now():
    """An app whose clock is pinned to NOW. Redirects are public, so no identity is needed."""
    async with running_app(Settings(), overrides={get_now: lambda: NOW}) as (_, client):
        yield client


# --- the status matrix ------------------------------------------------------------------------


async def test_redirect_302(at_now: AsyncClient, owner: User, db: AsyncSession) -> None:
    await make_link(db, owner, "go00001", destination_url=DEST)
    resp = await at_now.get("/go00001")
    # 302 (not 301): a 301 would be cached by browsers forever and hide later clicks and edits.
    assert resp.status_code == 302
    assert resp.headers["location"] == DEST
    assert resp.content == b""


async def test_unknown_404(at_now: AsyncClient) -> None:
    resp = await at_now.get("/nope123")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "not_found", "message": "link not found"}}


async def test_disabled_404(at_now: AsyncClient, owner: User, db: AsyncSession) -> None:
    await make_link(db, owner, "off0001", is_active=False)
    resp = await at_now.get("/off0001")
    assert resp.status_code == 404
    # Identical to an unknown code: visitors cannot tell a disabled link ever existed.
    assert resp.json() == {"error": {"code": "not_found", "message": "link not found"}}


async def test_disabled_404_even_if_expired(
    at_now: AsyncClient, owner: User, db: AsyncSession
) -> None:
    await make_link(db, owner, "offexp1", is_active=False, expires_at=NOW - timedelta(days=1))
    assert (await at_now.get("/offexp1")).status_code == 404  # disabled beats expired


async def test_expired_410(at_now: AsyncClient, owner: User, db: AsyncSession) -> None:
    await make_link(db, owner, "old0001", expires_at=NOW - timedelta(seconds=1))
    resp = await at_now.get("/old0001")
    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "gone"


@pytest.mark.parametrize(
    ("expires_at", "expected"),
    [
        (NOW - timedelta(seconds=1), 410),
        (NOW, 410),  # expires_at <= now means expired
        (NOW + timedelta(seconds=1), 302),
        (None, 302),
    ],
    ids=["just-expired", "exactly-now", "just-valid", "no-expiry"],
)
async def test_expiry_boundary(
    at_now: AsyncClient, owner: User, db: AsyncSession, expires_at: datetime | None, expected: int
) -> None:
    await make_link(db, owner, "edge001", expires_at=expires_at)
    assert (await at_now.get("/edge001")).status_code == expected


async def test_codes_are_case_sensitive(at_now: AsyncClient, owner: User, db: AsyncSession) -> None:
    await make_link(db, owner, "AbCdEfG", destination_url="https://upper.example/")
    assert (await at_now.get("/AbCdEfG")).status_code == 302
    assert (await at_now.get("/abcdefg")).status_code == 404
    assert (await at_now.get("/ABCDEFG")).status_code == 404


async def test_query_string_on_the_short_url_is_ignored(
    at_now: AsyncClient, owner: User, db: AsyncSession
) -> None:
    await make_link(db, owner, "qs00001", destination_url="https://example.com/x")
    resp = await at_now.get("/qs00001?fbclid=abc")
    assert (resp.status_code, resp.headers["location"]) == (302, "https://example.com/x")


async def test_redirect_is_public_and_creates_no_user(
    at_now: AsyncClient, owner: User, db: AsyncSession
) -> None:
    """Visitors are anonymous: following a link needs no identity (the dev user is not created)."""
    await make_link(db, owner, "pub0001")
    await at_now.get("/pub0001")
    assert (
        await db.execute(select(func.count()).select_from(User))
    ).scalar_one() == 1  # owner only


# --- HEAD -------------------------------------------------------------------------------------


async def test_head_same_status(at_now: AsyncClient, owner: User, db: AsyncSession) -> None:
    await make_link(db, owner, "okay001", destination_url=DEST)
    await make_link(db, owner, "off0001", is_active=False)
    await make_link(db, owner, "old0001", expires_at=NOW - timedelta(days=1))
    for code in ("okay001", "off0001", "old0001", "nope001"):
        got, head = await at_now.get(f"/{code}"), await at_now.head(f"/{code}")
        assert head.status_code == got.status_code, code
        assert head.headers.get("location") == got.headers.get("location"), code
        assert head.content == b"", code  # a HEAD response never has a body


# --- Postgres down ----------------------------------------------------------------------------


async def test_db_down_cache_miss_503() -> None:
    # (Until Redis exists, every request is a "cache miss": the whole path is Postgres.)
    async with running_app(Settings(database_url=DEAD_DB)) as (_, c):
        get, head = await c.get("/abc1234"), await c.head("/abc1234")
        health = await c.get("/healthz")
    assert get.status_code == 503
    assert get.json()["error"]["code"] == "unavailable"
    assert head.status_code == 503 and head.content == b""
    assert health.status_code == 200  # the process itself stays alive


@pytest.mark.parametrize(
    "path",
    [
        "/a",  # too short
        "/ab",
        "/" + "x" * 33,  # too long
        "/favicon.ico",
        "/robots.txt",
        "/.env",
        "/a%00b",  # NUL byte: must never reach a query
        "/a%20b",
        "/%F0%9F%98%80emoji",
        "/..%2f..%2fetc",
    ],
)
async def test_malformed_codes_are_404_and_never_touch_the_database(path: str) -> None:
    # With the database DEAD, a lookup would give 503. Getting 404 proves it was never queried.
    async with running_app(Settings(database_url=DEAD_DB)) as (_, c):
        resp = await c.get(path)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


# --- route order ------------------------------------------------------------------------------


async def test_routes_not_shadowed_by_redirect(owner: User, api_as: ApiAs) -> None:
    """If the redirect router were registered first, /healthz would be treated as a short code
    and answer 404. (This tests behaviour, not FastAPI's internal route list.)"""
    settings = Settings(database_url=Settings().database_url, enable_docs=True)
    async with api_as(owner, now=NOW, settings=settings) as (_, c):
        assert (await c.get("/healthz")).json() == {"status": "ok"}
        assert (await c.get("/readyz")).status_code == 200
        assert (await c.get("/api/v1/links")).status_code == 200
        assert (await c.get("/api/docs")).status_code == 200
        # "api" is a reserved word, so no link can ever use it: plain 404 from the redirect route.
        assert (await c.get("/api")).status_code == 404


async def test_unicode_destination_is_sent_as_punycode(
    at_now: AsyncClient, owner: User, db: AsyncSession
) -> None:
    await make_link(db, owner, "idn0001", destination_url="https://bücher.example/straße")
    resp = await at_now.get("/idn0001")
    assert resp.status_code == 302
    # Host in punycode (a percent-encoded host would point somewhere else); path percent-encoded.
    assert resp.headers["location"] == "https://xn--bcher-kva.example/stra%C3%9Fe"
