"""The links endpoints use REAL authentication (task P2.6, replaces the dev user of D37).

No identity overrides here: every request logs in through the API like a browser would, so this
covers the whole path (cookie, CSRF, Origin, session lookup, ownership scoping).
"""

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Link, User
from tests.conftest import TEST_DATABASE_URL
from tests.integration.browser import ORIGIN, PASSWORD_HASH, Browser
from tests.integration.conftest import running_app
from tests.integration.factories import make_link, make_user

LINKS = "/api/v1/links"


def settings() -> Settings:
    return Settings(database_url=TEST_DATABASE_URL, allowed_origins=ORIGIN)


@pytest.fixture
async def alice(db: AsyncSession) -> User:
    return await make_user(db, "alice@example.com", password_hash=PASSWORD_HASH)


@pytest.fixture
async def bob(db: AsyncSession) -> User:
    return await make_user(db, "bob@example.com", password_hash=PASSWORD_HASH)


@pytest.fixture
async def alice_browser(alice: User) -> AsyncIterator[Browser]:
    async with running_app(settings()) as (_, c):
        yield await Browser(c).login("alice@example.com")


@pytest.fixture
async def anonymous() -> AsyncIterator[AsyncClient]:
    async with running_app(settings()) as (_, c):
        yield c


# --- no identity, no access -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", LINKS),
        ("GET", LINKS),
        ("GET", f"{LINKS}/00000000-0000-0000-0000-000000000001"),
        ("PATCH", f"{LINKS}/00000000-0000-0000-0000-000000000001"),
        ("DELETE", f"{LINKS}/00000000-0000-0000-0000-000000000001"),
    ],
)
async def test_every_links_endpoint_is_401_without_a_session(
    anonymous: AsyncClient, method: str, path: str
) -> None:
    body = {"destination_url": "https://example.com/x"} if method in {"POST", "PATCH"} else None
    resp = await anonymous.request(method, path, json=body)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    assert len(resp.headers.get_list("set-cookie")) == 2  # both auth cookies cleared


async def test_no_user_is_created_by_unauthenticated_requests(
    anonymous: AsyncClient, db: AsyncSession
) -> None:
    await anonymous.get(LINKS)
    await anonymous.post(LINKS, json={"destination_url": "https://example.com/x"})
    assert (await db.execute(select(func.count()).select_from(User))).scalar_one() == 0


async def test_the_redirect_stays_public(
    anonymous: AsyncClient, alice: User, db: AsyncSession
) -> None:
    await make_link(db, alice, "pub0001", destination_url="https://example.com/public")
    resp = await anonymous.get("/pub0001")
    assert (resp.status_code, resp.headers["location"]) == (302, "https://example.com/public")


# --- ownership --------------------------------------------------------------------------------


async def test_a_created_link_belongs_to_the_logged_in_user(
    alice_browser: Browser, alice: User, db: AsyncSession
) -> None:
    resp = await alice_browser.post(LINKS, json={"destination_url": "https://example.com/a"})
    assert resp.status_code == 201
    owner = (
        await db.execute(select(Link.user_id).where(Link.id == resp.json()["id"]))
    ).scalar_one()
    assert owner == alice.id


async def test_users_cannot_see_or_touch_each_others_links(
    alice_browser: Browser, alice: User, bob: User, db: AsyncSession
) -> None:
    theirs = await make_link(
        db, bob, "bob0001", destination_url="https://example.com/bob", title="Private"
    )
    mine = await make_link(db, alice, "ali0001")
    link_id = str(theirs.id)

    got = await alice_browser.get(f"{LINKS}/{link_id}")
    patched = await alice_browser.patch(f"{LINKS}/{link_id}", json={"title": "pwned"})
    deleted = await alice_browser.delete(f"{LINKS}/{link_id}")
    listing = (await alice_browser.get(LINKS)).json()
    search = (await alice_browser.get(f"{LINKS}?q=bob")).json()

    # 404 (not 403) on every verb: Alice cannot even tell that the id exists.
    assert (got.status_code, patched.status_code, deleted.status_code) == (404, 404, 404)
    assert got.json() == patched.json() == deleted.json()
    assert [i["code"] for i in listing["items"]] == ["ali0001"] and listing["total"] == 1
    assert search["items"] == [] and search["total"] == 0  # search is scoped too
    await db.refresh(theirs)
    assert theirs.title == "Private"  # untouched
    assert mine.user_id == alice.id


async def test_two_users_see_only_their_own_lists(alice: User, bob: User, db: AsyncSession) -> None:
    await make_link(db, alice, "ali0001")
    await make_link(db, bob, "bob0001")
    await make_link(db, bob, "bob0002")
    async with running_app(settings()) as (_, a), running_app(settings()) as (_, b):
        ba, bb = (
            await Browser(a).login("alice@example.com"),
            await Browser(b).login("bob@example.com"),
        )
        assert (await ba.get(LINKS)).json()["total"] == 1
        assert (await bb.get(LINKS)).json()["total"] == 2


# --- CSRF applies to the links endpoints too --------------------------------------------------


async def test_links_writes_need_the_csrf_token_and_origin(
    alice_browser: Browser, alice: User, db: AsyncSession
) -> None:
    http = alice_browser.http
    body = {"destination_url": "https://example.com/x"}
    assert (await http.post(LINKS, json=body)).status_code == 403  # no token, no Origin
    bad_origin = {"X-CSRF-Token": http.cookies["lf_csrf"], "Origin": "https://evil.example"}
    assert (await http.post(LINKS, json=body, headers=bad_origin)).status_code == 403
    no_token = {"Origin": ORIGIN}
    assert (await http.post(LINKS, json=body, headers=no_token)).status_code == 403
    assert (await db.execute(select(func.count()).select_from(Link))).scalar_one() == 0

    link = await make_link(db, alice, "csrf001")
    assert (await http.patch(f"{LINKS}/{link.id}", json={"title": "x"})).status_code == 403
    assert (await http.delete(f"{LINKS}/{link.id}")).status_code == 403
    assert (await alice_browser.get(f"{LINKS}/{link.id}")).status_code == 200  # reads need none
    assert (await alice_browser.delete(f"{LINKS}/{link.id}")).status_code == 204  # proper headers


async def test_after_logout_the_old_cookie_no_longer_works(alice_browser: Browser) -> None:
    http = alice_browser.http
    cookie = http.cookies["lf_session"]
    assert (await alice_browser.post("/api/v1/auth/logout")).status_code == 204
    resp = await http.get(LINKS, headers={"Cookie": f"lf_session={cookie}"})
    assert resp.status_code == 401
