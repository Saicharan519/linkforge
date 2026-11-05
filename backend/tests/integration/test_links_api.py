"""Link CRUD API (PRD F2). Real app, real Postgres; identity is stubbed until P2 adds real auth."""

import re
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import ClickEvent, Link, LinkDailyStat, User
from tests.conftest import TEST_DATABASE_URL
from tests.integration.conftest import ApiAs, client_for
from tests.integration.factories import make_link, make_user, new_click

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)  # the pinned "current time" for these tests
FUTURE = (NOW + timedelta(days=30)).isoformat()
PAST = (NOW - timedelta(days=1)).isoformat()
URL = "https://example.com/page"


@pytest.fixture
async def owner(db: AsyncSession) -> User:
    return await make_user(db, "owner@example.com")


@pytest.fixture
async def other(db: AsyncSession) -> User:
    return await make_user(db, "other@example.com")


def fields(resp) -> set[str]:
    """The field names listed in a 422 response."""
    return {d["field"] for d in resp.json()["error"]["details"]}


# --- create -----------------------------------------------------------------------------------


async def test_create_minimal(api_as: ApiAs, owner: User, db: AsyncSession) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.post("/api/v1/links", json={"destination_url": URL})
    assert resp.status_code == 201
    link = resp.json()
    assert re.fullmatch(r"[A-Za-z0-9]{7}", link["code"])
    assert link["short_url"] == f"http://localhost:8000/{link['code']}"
    assert link["destination_url"] == URL
    assert (link["title"], link["expires_at"]) == (None, None)
    assert (link["is_active"], link["status"], link["total_clicks"]) == (True, "active", 0)
    uuid.UUID(link["id"])
    # It really is stored, and owned by the caller.
    row = (await db.execute(select(Link))).scalar_one()
    assert (str(row.id), row.user_id) == (link["id"], owner.id)


async def test_create_with_alias_title_and_expiry(api_as: ApiAs, owner: User) -> None:
    body = {"destination_url": URL, "alias": "my-link_1", "title": "Docs", "expires_at": FUTURE}
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.post("/api/v1/links", json=body)
    assert resp.status_code == 201
    link = resp.json()
    assert (link["code"], link["title"]) == ("my-link_1", "Docs")
    assert datetime.fromisoformat(link["expires_at"]) == NOW + timedelta(days=30)


async def test_alias_case_preserved(api_as: ApiAs, owner: User) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        a = await c.post("/api/v1/links", json={"destination_url": URL, "alias": "MyLink"})
        b = await c.post("/api/v1/links", json={"destination_url": URL, "alias": "mylink"})
    assert (a.status_code, a.json()["code"]) == (201, "MyLink")  # stored exactly as typed
    assert (b.status_code, b.json()["code"]) == (201, "mylink")  # a different code


async def test_duplicate_alias_409(api_as: ApiAs, owner: User, other: User) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        first = await c.post("/api/v1/links", json={"destination_url": URL, "alias": "Taken1"})
        again = await c.post("/api/v1/links", json={"destination_url": URL, "alias": "Taken1"})
    async with api_as(other, now=NOW) as (_, c2):
        # Codes are global, so another user cannot take it either.
        theirs = await c2.post("/api/v1/links", json={"destination_url": URL, "alias": "Taken1"})
    assert first.status_code == 201
    for resp in (again, theirs):
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "alias_taken"


@pytest.mark.parametrize("alias", ["ab", "a" * 33, "has space", "dot.com", "a/b", ""])
async def test_invalid_alias_422(api_as: ApiAs, owner: User, alias: str) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.post("/api/v1/links", json={"destination_url": URL, "alias": alias})
    assert resp.status_code == 422
    assert fields(resp) == {"alias"}


@pytest.mark.parametrize("alias", ["admin", "Admin", "API", "healthz", "Dashboard"])
async def test_reserved_alias_422(api_as: ApiAs, owner: User, alias: str) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.post("/api/v1/links", json={"destination_url": URL, "alias": alias})
    assert resp.status_code == 422
    assert "reserved" in resp.json()["error"]["details"][0]["message"]


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "ftp://example.com/x",
        "not a url",
        "example.com",
        "http://localhost:8000/abc",  # our own short domain in the test settings
        "https://example.com/" + "a" * 2050,
    ],
)
async def test_invalid_destination_422(api_as: ApiAs, owner: User, url: str) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.post("/api/v1/links", json={"destination_url": url})
    assert resp.status_code == 422
    assert fields(resp) == {"destination_url"}


async def test_all_problems_are_reported_together(api_as: ApiAs, owner: User) -> None:
    body = {"destination_url": "javascript:x", "alias": "ab", "expires_at": PAST}
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.post("/api/v1/links", json=body)
    assert resp.status_code == 422
    assert fields(resp) == {"destination_url", "alias", "expires_at"}


@pytest.mark.parametrize("when", [PAST, NOW.isoformat()], ids=["past", "exactly-now"])
async def test_expires_at_must_be_in_the_future(api_as: ApiAs, owner: User, when: str) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.post("/api/v1/links", json={"destination_url": URL, "expires_at": when})
    assert resp.status_code == 422
    assert fields(resp) == {"expires_at"}


async def test_expires_at_without_timezone_422(api_as: ApiAs, owner: User) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.post(
            "/api/v1/links", json={"destination_url": URL, "expires_at": "2030-01-01T00:00:00"}
        )
    assert resp.status_code == 422
    assert fields(resp) == {"expires_at"}


@pytest.mark.parametrize("title", ["", "x" * 201])
async def test_title_limits_422(api_as: ApiAs, owner: User, title: str) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.post("/api/v1/links", json={"destination_url": URL, "title": title})
    assert resp.status_code == 422
    assert fields(resp) == {"title"}


async def test_title_at_limit_ok(api_as: ApiAs, owner: User) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.post("/api/v1/links", json={"destination_url": URL, "title": "x" * 200})
    assert resp.status_code == 201


async def test_unknown_and_missing_fields_422(api_as: ApiAs, owner: User) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        typo = await c.post("/api/v1/links", json={"destination_url": URL, "tittle": "oops"})
        empty = await c.post("/api/v1/links", json={})
    assert typo.status_code == 422 and fields(typo) == {"tittle"}
    assert empty.status_code == 422 and fields(empty) == {"destination_url"}


async def test_short_url_uses_configured_base(api_as: ApiAs, owner: User) -> None:
    settings = Settings(database_url=TEST_DATABASE_URL, short_url_base="https://go.example.com/")
    async with api_as(owner, now=NOW, settings=settings) as (_, c):
        resp = await c.post("/api/v1/links", json={"destination_url": URL, "alias": "abc"})
    assert resp.json()["short_url"] == "https://go.example.com/abc"  # no double slash


# --- status -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({}, "active"),
        ({"expires_at": NOW + timedelta(days=1)}, "active"),
        ({"expires_at": NOW - timedelta(days=1)}, "expired"),
        ({"expires_at": NOW}, "expired"),  # expires_at <= now counts as expired
        ({"is_active": False}, "disabled"),
        ({"is_active": False, "expires_at": NOW - timedelta(days=1)}, "disabled"),  # beats expired
    ],
    ids=["no-expiry", "future", "past", "exactly-now", "disabled", "disabled-beats-expired"],
)
async def test_status(
    api_as: ApiAs, owner: User, db: AsyncSession, extra: dict, expected: str
) -> None:
    link = await make_link(db, owner, "stat001", **extra)
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.get(f"/api/v1/links/{link.id}")
    assert resp.json()["status"] == expected


# --- list -------------------------------------------------------------------------------------


async def test_list_empty(api_as: ApiAs, owner: User) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.get("/api/v1/links")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}


async def test_list_newest_first(api_as: ApiAs, owner: User, db: AsyncSession) -> None:
    for i, code in enumerate(["oldest1", "middle1", "newest1"]):
        await make_link(db, owner, code, created_at=NOW - timedelta(days=10 - i))
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.get("/api/v1/links")
    assert [item["code"] for item in resp.json()["items"]] == ["newest1", "middle1", "oldest1"]


async def test_list_search(api_as: ApiAs, owner: User, db: AsyncSession) -> None:
    await make_link(db, owner, "alpha01", title="Quarterly Report")
    await make_link(db, owner, "beta002", title=None, destination_url="https://shop.example.org/x")
    await make_link(db, owner, "gamma03", title="50% off sale")
    await make_link(db, owner, "delta04", title="500 off sale")
    await make_link(db, owner, "eps_005", title="a_b")
    await make_link(db, owner, "zeta006", title="axb")

    async def search(c, q: str) -> set[str]:
        resp = await c.get("/api/v1/links", params={"q": q})
        assert resp.status_code == 200
        return {item["code"] for item in resp.json()["items"]}

    async with api_as(owner, now=NOW) as (_, c):
        assert await search(c, "ALPHA") == {"alpha01"}  # by code, case-insensitive
        assert await search(c, "quarterly") == {"alpha01"}  # by title
        assert await search(c, "shop.example.org") == {"beta002"}  # by destination URL
        assert await search(c, "50%") == {"gamma03"}  # "%" is literal, not a wildcard
        assert await search(c, "a_b") == {"eps_005"}  # "_" is literal, not a wildcard
        assert await search(c, "no-such-thing") == set()
        assert len(await search(c, "   ")) == 6  # blank search = no filter


async def test_list_status_filter(api_as: ApiAs, owner: User, db: AsyncSession) -> None:
    await make_link(db, owner, "active1")
    await make_link(db, owner, "future1", expires_at=NOW + timedelta(days=1))
    await make_link(db, owner, "expired", expires_at=NOW - timedelta(days=1))
    await make_link(db, owner, "disabl1", is_active=False)
    await make_link(db, owner, "disexp1", is_active=False, expires_at=NOW - timedelta(days=1))

    async def codes(c, status: str) -> set[str]:
        resp = await c.get("/api/v1/links", params={"status": status})
        return {item["code"] for item in resp.json()["items"]}

    async with api_as(owner, now=NOW) as (_, c):
        assert await codes(c, "active") == {"active1", "future1"}
        assert await codes(c, "expired") == {"expired"}
        assert await codes(c, "disabled") == {"disabl1", "disexp1"}  # disabled beats expired


async def test_list_invalid_status_422(api_as: ApiAs, owner: User) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.get("/api/v1/links", params={"status": "bogus"})
    assert resp.status_code == 422 and fields(resp) == {"status"}


async def test_pagination(api_as: ApiAs, owner: User, db: AsyncSession) -> None:
    for i in range(25):
        await make_link(db, owner, f"page{i:03d}", created_at=NOW - timedelta(minutes=i))
    async with api_as(owner, now=NOW) as (_, c):
        first = (await c.get("/api/v1/links")).json()  # default page_size is 20
        second = (await c.get("/api/v1/links", params={"page": 2})).json()
        small = (await c.get("/api/v1/links", params={"page_size": 5, "page": 3})).json()
        beyond = (await c.get("/api/v1/links", params={"page": 99})).json()
    assert (first["total"], len(first["items"]), first["page"], first["page_size"]) == (
        25,
        20,
        1,
        20,
    )
    assert (len(second["items"]), second["page"]) == (5, 2)
    assert [i["code"] for i in small["items"]] == [f"page{n:03d}" for n in range(10, 15)]
    assert beyond["items"] == [] and beyond["total"] == 25
    pages = {i["code"] for i in first["items"]} | {i["code"] for i in second["items"]}
    assert len(pages) == 25  # no link repeated or lost across pages


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"page": 0}, "page"),
        ({"page": -1}, "page"),
        ({"page": "abc"}, "page"),
        ({"page_size": 0}, "page_size"),
        ({"page_size": 101}, "page_size"),
        ({"q": "x" * 201}, "q"),
    ],
)
async def test_list_invalid_params_422(
    api_as: ApiAs, owner: User, params: dict, field: str
) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.get("/api/v1/links", params=params)
    assert resp.status_code == 422 and fields(resp) == {field}


async def test_page_size_100_is_allowed(api_as: ApiAs, owner: User) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.get("/api/v1/links", params={"page_size": 100})
    assert resp.status_code == 200 and resp.json()["page_size"] == 100


# --- get + total_clicks -----------------------------------------------------------------------


async def test_get_link(api_as: ApiAs, owner: User, db: AsyncSession) -> None:
    link = await make_link(db, owner, "get0001", title="Hello")
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.get(f"/api/v1/links/{link.id}")
    assert resp.status_code == 200
    assert (resp.json()["code"], resp.json()["title"]) == ("get0001", "Hello")


async def test_get_unknown_404_and_malformed_id_422(api_as: ApiAs, owner: User) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        unknown = await c.get(f"/api/v1/links/{uuid.uuid4()}")
        malformed = await c.get("/api/v1/links/not-a-uuid")
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "not_found"
    assert malformed.status_code == 422 and fields(malformed) == {"id"}


async def test_total_clicks_sums_daily_rollups(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    busy = await make_link(db, owner, "busy001")
    await make_link(db, owner, "quiet01")
    db.add_all(
        [
            LinkDailyStat(link_id=busy.id, day=date(2026, 11, 4), clicks=3),
            LinkDailyStat(link_id=busy.id, day=date(2026, 11, 5), clicks=4),
        ]
    )
    await db.commit()
    async with api_as(owner, now=NOW) as (_, c):
        one = (await c.get(f"/api/v1/links/{busy.id}")).json()
        listing = (await c.get("/api/v1/links")).json()
    assert one["total_clicks"] == 7
    assert {i["code"]: i["total_clicks"] for i in listing["items"]} == {"busy001": 7, "quiet01": 0}


async def test_list_total_clicks_uses_one_query(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    for i in range(8):
        link = await make_link(db, owner, f"many{i:03d}")
        db.add(LinkDailyStat(link_id=link.id, day=date(2026, 11, 5), clicks=i))
    await db.commit()

    statements: list[str] = []
    async with api_as(owner, now=NOW) as (app, c):
        event.listen(
            app.state.engine.sync_engine,
            "before_cursor_execute",
            lambda conn, cur, stmt, params, ctx, many: statements.append(stmt),
        )
        resp = await c.get("/api/v1/links")
    assert len(resp.json()["items"]) == 8
    # Eight links, but the rollup table is read exactly once: no N+1.
    assert sum("link_daily_stats" in s for s in statements) == 1


# --- patch ------------------------------------------------------------------------------------


async def test_patch_omitted_fields_are_unchanged(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    expires = NOW + timedelta(days=5)
    link = await make_link(db, owner, "patch01", title="Keep me", expires_at=expires)
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.patch(f"/api/v1/links/{link.id}", json={"is_active": False})
    body = resp.json()
    assert resp.status_code == 200
    assert (body["title"], body["destination_url"]) == ("Keep me", "https://example.com/page")
    assert datetime.fromisoformat(body["expires_at"]) == expires
    assert (body["is_active"], body["status"]) == (False, "disabled")


async def test_patch_null_clears_title(api_as: ApiAs, owner: User, db: AsyncSession) -> None:
    link = await make_link(db, owner, "patch02", title="Remove me")
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.patch(f"/api/v1/links/{link.id}", json={"title": None})
    assert resp.status_code == 200 and resp.json()["title"] is None


async def test_patch_null_clears_expires_at(api_as: ApiAs, owner: User, db: AsyncSession) -> None:
    link = await make_link(db, owner, "patch03", expires_at=NOW - timedelta(days=1))
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.patch(f"/api/v1/links/{link.id}", json={"expires_at": None})
    assert resp.json()["expires_at"] is None
    assert resp.json()["status"] == "active"  # no expiry any more


@pytest.mark.parametrize("field", ["destination_url", "is_active"])
async def test_patch_null_not_allowed_for_required_fields(
    api_as: ApiAs, owner: User, db: AsyncSession, field: str
) -> None:
    link = await make_link(db, owner, "patch04")
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.patch(f"/api/v1/links/{link.id}", json={field: None})
    assert resp.status_code == 422 and fields(resp) == {field}


async def test_patch_validates_destination(api_as: ApiAs, owner: User, db: AsyncSession) -> None:
    link = await make_link(db, owner, "patch05")
    async with api_as(owner, now=NOW) as (_, c):
        bad = await c.patch(f"/api/v1/links/{link.id}", json={"destination_url": "javascript:x"})
        good = await c.patch(f"/api/v1/links/{link.id}", json={"destination_url": "https://new.io"})
    assert bad.status_code == 422 and fields(bad) == {"destination_url"}
    assert good.status_code == 200 and good.json()["destination_url"] == "https://new.io"


async def test_patch_cannot_change_the_code(api_as: ApiAs, owner: User, db: AsyncSession) -> None:
    link = await make_link(db, owner, "patch06")
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.patch(f"/api/v1/links/{link.id}", json={"code": "hijack1"})
    assert resp.status_code == 422 and fields(resp) == {"code"}


@pytest.mark.parametrize("when", [PAST, NOW.isoformat()], ids=["past", "exactly-now"])
async def test_patch_expires_at_must_be_in_the_future(
    api_as: ApiAs, owner: User, db: AsyncSession, when: str
) -> None:
    link = await make_link(db, owner, "patch07")
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.patch(f"/api/v1/links/{link.id}", json={"expires_at": when})
    assert resp.status_code == 422 and fields(resp) == {"expires_at"}


async def test_patch_can_extend_an_expired_link(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "patch08", expires_at=NOW - timedelta(days=1))
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.patch(f"/api/v1/links/{link.id}", json={"expires_at": FUTURE})
    assert resp.json()["status"] == "active"


async def test_patch_updates_updated_at(api_as: ApiAs, owner: User, db: AsyncSession) -> None:
    link = await make_link(db, owner, "patch09", updated_at=NOW - timedelta(days=1))
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.patch(f"/api/v1/links/{link.id}", json={"title": "x"})
    assert datetime.fromisoformat(resp.json()["updated_at"]) == NOW


async def test_patch_reenables_a_disabled_link(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "patch10", is_active=False)
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.patch(f"/api/v1/links/{link.id}", json={"is_active": True})
    assert resp.json()["status"] == "active"


# --- delete -----------------------------------------------------------------------------------


async def test_delete_link_cascades_click_data(
    api_as: ApiAs, owner: User, db: AsyncSession
) -> None:
    link = await make_link(db, owner, "del0001")
    db.add(new_click(link))
    db.add(LinkDailyStat(link_id=link.id, day=date(2026, 11, 5), clicks=1))
    await db.commit()

    async with api_as(owner, now=NOW) as (_, c):
        deleted = await c.delete(f"/api/v1/links/{link.id}")
        gone = await c.get(f"/api/v1/links/{link.id}")
        again = await c.delete(f"/api/v1/links/{link.id}")
    assert deleted.status_code == 204 and deleted.content == b""
    assert gone.status_code == 404
    assert again.status_code == 404
    for model in (Link, ClickEvent, LinkDailyStat):
        assert (await db.execute(select(func.count()).select_from(model))).scalar_one() == 0


# --- ownership isolation ----------------------------------------------------------------------


async def test_other_users_links_are_invisible_and_untouchable(
    api_as: ApiAs, owner: User, other: User, db: AsyncSession
) -> None:
    theirs = await make_link(db, other, "theirs1", title="Private")
    mine = await make_link(db, owner, "mine001")

    async with api_as(owner, now=NOW) as (_, c):
        get = await c.get(f"/api/v1/links/{theirs.id}")
        patch = await c.patch(f"/api/v1/links/{theirs.id}", json={"title": "pwned"})
        delete = await c.delete(f"/api/v1/links/{theirs.id}")
        listing = (await c.get("/api/v1/links")).json()

    # 404, not 403: the caller cannot even tell whether that id exists.
    assert (get.status_code, patch.status_code, delete.status_code) == (404, 404, 404)
    assert [i["code"] for i in listing["items"]] == ["mine001"] and listing["total"] == 1
    await db.refresh(theirs)  # unchanged and still there
    assert theirs.title == "Private"
    assert mine.user_id == owner.id


# --- the temporary dev user (D37) -------------------------------------------------------------


async def test_dev_user_owns_links_until_real_auth(db: AsyncSession) -> None:
    """With no identity override, requests act as one fixed development user."""
    async with client_for(Settings()) as c:
        a = await c.post("/api/v1/links", json={"destination_url": URL, "alias": "dev0001"})
        b = await c.post("/api/v1/links", json={"destination_url": URL, "alias": "dev0002"})
    assert (a.status_code, b.status_code) == (201, 201)
    users = (await db.execute(select(User.email))).scalars().all()
    assert users == ["dev@linkforge.local"]  # created once, reused


async def test_dev_user_refused_in_production() -> None:
    settings = Settings(database_url=TEST_DATABASE_URL, env="prod")
    async with client_for(settings) as c:
        resp = await c.post("/api/v1/links", json={"destination_url": URL})
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "unauthorized"


async def test_dev_user_is_temporary(client) -> None:
    """TRIPWIRE. Real auth does not exist yet, so /auth/me is unknown.

    When P2 adds the auth routes this test FAILS: that is the reminder to delete the dev-user
    dependency in app/api/deps.py (P2.6, decision D37) and this test.
    """
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 404


# --- the error shape (D30) --------------------------------------------------------------------


async def test_validation_error_shape(api_as: ApiAs, owner: User) -> None:
    async with api_as(owner, now=NOW) as (_, c):
        resp = await c.post("/api/v1/links", json={})
    assert resp.status_code == 422
    assert resp.json() == {
        "error": {
            "code": "validation_error",
            "message": "the request contains invalid data",
            "details": [{"field": "destination_url", "message": "Field required"}],
        }
    }


async def test_unknown_route_and_wrong_method_use_the_error_shape(client) -> None:
    missing = await client.get("/api/v1/nope")
    wrong = await client.delete("/api/v1/links")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    assert wrong.status_code == 405 and wrong.json()["error"]["code"] == "method_not_allowed"


async def test_unexpected_crash_is_a_clean_500() -> None:
    from tests.integration.conftest import running_app

    async with running_app(Settings()) as (app, c):

        async def boom() -> None:
            raise RuntimeError("secret internal detail")

        # A multi-segment path: the catch-all `/{code}` redirect route matches single segments only.
        app.add_api_route("/api/v1/boom", boom)
        resp = await c.get("/api/v1/boom")
    assert resp.status_code == 500
    assert resp.json() == {
        "error": {"code": "internal_error", "message": "an unexpected error occurred"}
    }  # no stack trace, no "secret internal detail"
