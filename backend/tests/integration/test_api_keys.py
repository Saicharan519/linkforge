"""API keys and the per-user create limiter (task P2.7, ARCHITECTURE §2.4, §5, D13, D26)."""

import hashlib
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_now
from app.config import Settings
from app.models import ApiKey, Link, User
from app.services import api_keys as service
from tests.conftest import TEST_DATABASE_URL
from tests.integration.browser import ORIGIN, PASSWORD, PASSWORD_HASH, Browser
from tests.integration.conftest import running_app
from tests.integration.factories import make_link, make_user

KEYS = "/api/v1/api-keys"
LINKS = "/api/v1/links"
KEY_SHAPE = re.compile(r"lf_[A-Za-z0-9]{8}_[A-Za-z0-9_-]{43}")
DEAD_REDIS = "redis://127.0.0.1:1/0"


def settings_with(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": TEST_DATABASE_URL,
        "allowed_origins": ORIGIN,
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
async def alice(db: AsyncSession) -> User:
    return await make_user(db, "alice@example.com", password_hash=PASSWORD_HASH)


@pytest.fixture
async def bob(db: AsyncSession) -> User:
    return await make_user(db, "bob@example.com", password_hash=PASSWORD_HASH)


@pytest.fixture
async def browser(alice: User) -> AsyncIterator[Browser]:
    async with running_app(settings_with()) as (_, c):
        yield await Browser(c).login("alice@example.com")


@pytest.fixture
async def anonymous() -> AsyncIterator[AsyncClient]:
    """No cookies: how a script or CI job talks to the API."""
    async with running_app(settings_with()) as (_, c):
        yield c


async def new_key(browser: Browser, name: str = "ci") -> dict[str, str]:
    resp = await browser.post(KEYS, json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def key_header(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


# --- create -----------------------------------------------------------------------------------


async def test_create_returns_the_plaintext_once_and_stores_only_a_hash(
    browser: Browser, alice: User, db: AsyncSession
) -> None:
    body = await new_key(browser, "deploy script")
    key = body["key"]
    assert KEY_SHAPE.fullmatch(key)
    assert body["prefix"] == key[3:11]
    assert body["name"] == "deploy script"
    assert body["last_used_at"] is None and body["revoked_at"] is None

    row = (await db.execute(select(ApiKey).where(ApiKey.id == body["id"]))).scalar_one()
    assert row.key_hash == hashlib.sha256(key.encode()).hexdigest()
    assert row.prefix == body["prefix"] and row.user_id == alice.id
    # The plaintext (and its secret part) appear in no stored column.
    secret = key.split("_", 2)[2]
    stored = " ".join(str(v) for v in (row.name, row.prefix, row.key_hash))
    assert key not in stored and secret not in stored


async def test_the_plaintext_can_never_be_read_again(browser: Browser) -> None:
    body = await new_key(browser)
    secret = body["key"].split("_", 2)[2]
    listing = await browser.get(KEYS)
    assert listing.status_code == 200
    assert "key" not in listing.json()[0]  # only prefix, name, dates
    assert secret not in listing.text
    assert set(listing.json()[0]) == {
        "id",
        "name",
        "prefix",
        "created_at",
        "last_used_at",
        "revoked_at",
    }


async def test_keys_are_unique_and_random(browser: Browser) -> None:
    keys = {(await new_key(browser, f"k{i}"))["key"] for i in range(5)}
    assert len(keys) == 5
    assert len({k[3:11] for k in keys}) == 5


@pytest.mark.parametrize(
    "payload",
    [{}, {"name": ""}, {"name": "x" * 65}, {"name": "ok", "extra": 1}, {"name": 5}],
)
async def test_create_validates_the_name(browser: Browser, payload: dict[str, object]) -> None:
    resp = await browser.post(KEYS, json=payload)
    assert resp.status_code == 422


async def test_a_64_character_name_is_fine(browser: Browser) -> None:
    assert (await browser.post(KEYS, json={"name": "n" * 64})).status_code == 201


async def test_prefix_collisions_are_retried(
    browser: Browser, monkeypatch: pytest.MonkeyPatch, db: AsyncSession
) -> None:
    first = await new_key(browser)
    draws = iter(
        [
            (f"lf_{first['prefix']}_{'a' * 43}", first["prefix"]),  # collides with the first key
            (f"lf_NEWPREFX_{'b' * 43}", "NEWPREFX"),
        ]
    )
    monkeypatch.setattr(service, "_new_key", lambda: next(draws))
    second = await browser.post(KEYS, json={"name": "second"})
    assert second.status_code == 201
    assert second.json()["prefix"] == "NEWPREFX"


async def test_managing_keys_needs_a_session_csrf_and_origin(
    browser: Browser, anonymous: AsyncClient
) -> None:
    assert (await anonymous.post(KEYS, json={"name": "x"})).status_code == 401
    assert (await anonymous.get(KEYS)).status_code == 401
    http = browser.http
    assert (await http.post(KEYS, json={"name": "x"})).status_code == 403  # no token, no Origin
    evil = {"X-CSRF-Token": http.cookies["lf_csrf"], "Origin": "https://evil.example"}
    assert (await http.post(KEYS, json={"name": "x"}, headers=evil)).status_code == 403
    assert (await browser.get(KEYS)).json() == []  # nothing was created by the refused requests


async def test_an_api_key_cannot_manage_keys_or_the_account(
    browser: Browser, anonymous: AsyncClient
) -> None:
    key = (await new_key(browser))["key"]
    for method, path in (
        ("POST", KEYS),
        ("GET", KEYS),
        ("GET", "/api/v1/auth/me"),
        ("POST", "/api/v1/auth/change-password"),
        ("POST", "/api/v1/auth/logout"),
    ):
        resp = await anonymous.request(method, path, headers=key_header(key), json={"name": "x"})
        assert resp.status_code == 401, (method, path)  # a leaked key cannot mint more keys


# --- list -------------------------------------------------------------------------------------


async def test_list_shows_only_my_keys_newest_first(
    browser: Browser, bob: User, db: AsyncSession
) -> None:
    first = await new_key(browser, "first")
    second = await new_key(browser, "second")
    # Two creations can land within the same clock tick under load; make the order unambiguous.
    for key, minutes_ago in ((first, 2), (second, 1)):
        await db.execute(
            update(ApiKey)
            .where(ApiKey.id == key["id"])
            .values(created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago))
        )
    db.add(ApiKey(user_id=bob.id, name="bobs", prefix="BOBPREFX", key_hash="h"))
    await db.commit()
    listing = (await browser.get(KEYS)).json()
    assert [k["id"] for k in listing] == [second["id"], first["id"]]
    assert "BOBPREFX" not in {k["prefix"] for k in listing}


# --- using a key ------------------------------------------------------------------------------


async def test_a_key_works_without_cookies_csrf_or_origin(
    browser: Browser, anonymous: AsyncClient, alice: User, db: AsyncSession
) -> None:
    key = key_header((await new_key(browser))["key"])
    created = await anonymous.post(
        LINKS, json={"destination_url": "https://example.com/k"}, headers=key
    )
    assert created.status_code == 201  # a POST with no CSRF token and no Origin
    link_id = created.json()["id"]
    assert (
        await db.execute(select(Link.user_id).where(Link.id == link_id))
    ).scalar_one() == alice.id

    assert (await anonymous.get(LINKS, headers=key)).json()["total"] == 1
    assert (await anonymous.get(f"{LINKS}/{link_id}", headers=key)).status_code == 200
    patched = await anonymous.patch(f"{LINKS}/{link_id}", json={"title": "t"}, headers=key)
    assert patched.status_code == 200
    assert (await anonymous.delete(f"{LINKS}/{link_id}", headers=key)).status_code == 204


async def test_a_key_only_sees_its_owners_links(
    browser: Browser, anonymous: AsyncClient, bob: User, db: AsyncSession
) -> None:
    theirs = await make_link(db, bob, "bob0001")
    key = key_header((await new_key(browser))["key"])
    assert (await anonymous.get(f"{LINKS}/{theirs.id}", headers=key)).status_code == 404
    assert (await anonymous.delete(f"{LINKS}/{theirs.id}", headers=key)).status_code == 404
    assert (await anonymous.get(LINKS, headers=key)).json()["total"] == 0


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "garbage",
        "lf_",
        "lf_short_" + "a" * 43,
        "lf_ABCDEFGH_" + "a" * 42,  # one character short
        "lf_ABCDEFGH_" + "a" * 44,  # one too long
        "lf_ABCDEFGH_" + "a" * 42 + "*",
        "xx_ABCDEFGH_" + "a" * 43,
        "lf_ABCD-FGH_" + "a" * 43,  # a hyphen is not allowed in the prefix
        "'; DROP TABLE api_keys;--",
    ],
)
async def test_malformed_keys_are_401(anonymous: AsyncClient, bad: str) -> None:
    resp = await anonymous.get(LINKS, headers=key_header(bad))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_unknown_prefix_and_wrong_secret_are_401_with_the_same_answer(
    browser: Browser, anonymous: AsyncClient
) -> None:
    good = (await new_key(browser))["key"]
    wrong_secret = good[:-3] + ("AAA" if not good.endswith("AAA") else "BBB")
    unknown = "lf_ZZZZZZZZ_" + "a" * 43
    a = await anonymous.get(LINKS, headers=key_header(wrong_secret))
    b = await anonymous.get(LINKS, headers=key_header(unknown))
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


async def test_a_key_401_never_clears_cookies(browser: Browser) -> None:
    resp = await browser.http.get(LINKS, headers=key_header("lf_ZZZZZZZZ_" + "a" * 43))
    assert resp.status_code == 401
    assert not resp.headers.get_list("set-cookie")


async def test_the_key_header_wins_over_a_valid_session_cookie(browser: Browser) -> None:
    """D26: one credential, one answer. A bad key is 401 even when the cookie is perfectly valid."""
    assert (await browser.get(LINKS)).status_code == 200  # the cookie alone works
    resp = await browser.http.get(LINKS, headers=key_header("lf_ZZZZZZZZ_" + "a" * 43))
    assert resp.status_code == 401


async def test_a_valid_key_ignores_a_broken_session_cookie(
    browser: Browser, anonymous: AsyncClient
) -> None:
    key = (await new_key(browser))["key"]
    resp = await anonymous.get(
        LINKS, headers={**key_header(key), "Cookie": "lf_session=" + "x" * 43}
    )
    assert resp.status_code == 200
    assert not resp.headers.get_list("set-cookie")


# --- revoke -----------------------------------------------------------------------------------


async def test_a_revoked_key_is_401_immediately(
    browser: Browser, anonymous: AsyncClient, db: AsyncSession
) -> None:
    body = await new_key(browser)
    key = key_header(body["key"])
    assert (await anonymous.get(LINKS, headers=key)).status_code == 200

    assert (await browser.delete(f"{KEYS}/{body['id']}")).status_code == 204
    after = await anonymous.get(LINKS, headers=key)
    assert after.status_code == 401  # the very next request
    listing = (await browser.get(KEYS)).json()
    assert listing[0]["revoked_at"] is not None  # still listed, marked revoked


async def test_revoking_twice_is_fine_and_keeps_the_first_time(
    browser: Browser, db: AsyncSession
) -> None:
    body = await new_key(browser)
    assert (await browser.delete(f"{KEYS}/{body['id']}")).status_code == 204
    first = (await browser.get(KEYS)).json()[0]["revoked_at"]
    assert (await browser.delete(f"{KEYS}/{body['id']}")).status_code == 204
    assert (await browser.get(KEYS)).json()[0]["revoked_at"] == first


async def test_someone_elses_key_and_an_unknown_id_are_the_same_404(
    browser: Browser, bob: User, db: AsyncSession
) -> None:
    db.add(ApiKey(user_id=bob.id, name="bobs", prefix="BOBPREFX", key_hash="h"))
    await db.commit()
    theirs = (await db.execute(select(ApiKey.id).where(ApiKey.prefix == "BOBPREFX"))).scalar_one()
    foreign = await browser.delete(f"{KEYS}/{theirs}")
    unknown = await browser.delete(f"{KEYS}/00000000-0000-0000-0000-000000000001")
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()
    await db.rollback()
    still = (await db.execute(select(ApiKey.revoked_at).where(ApiKey.id == theirs))).scalar_one()
    assert still is None  # Bob's key is untouched


async def test_revoking_needs_csrf_and_a_session(browser: Browser, anonymous: AsyncClient) -> None:
    body = await new_key(browser)
    assert (await browser.http.delete(f"{KEYS}/{body['id']}")).status_code == 403
    key = key_header(body["key"])
    assert (await anonymous.delete(f"{KEYS}/{body['id']}", headers=key)).status_code == 401
    assert (await anonymous.get(LINKS, headers=key)).status_code == 200  # still alive


async def test_a_disabled_account_s_keys_stop_working(
    browser: Browser, anonymous: AsyncClient, alice: User, db: AsyncSession
) -> None:
    key = key_header((await new_key(browser))["key"])
    await db.execute(update(User).where(User.id == alice.id).values(is_active=False))
    await db.commit()
    assert (await anonymous.get(LINKS, headers=key)).status_code == 401


# --- last_used_at -----------------------------------------------------------------------------


async def test_last_used_is_written_at_most_once_a_minute(
    browser: Browser, alice: User, db: AsyncSession
) -> None:
    key = (await new_key(browser))["key"]
    t0 = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
    clock = {"now": t0}
    async with running_app(settings_with(), overrides={get_now: lambda: clock["now"]}) as (_, c):

        async def last_used() -> datetime | None:
            await db.rollback()
            return (await db.execute(select(ApiKey.last_used_at))).scalar_one()

        assert await last_used() is None
        await c.get(LINKS, headers=key_header(key))
        assert await last_used() == t0  # first use is recorded

        clock["now"] = t0 + timedelta(seconds=30)
        await c.get(LINKS, headers=key_header(key))
        assert await last_used() == t0  # within a minute: no write

        clock["now"] = t0 + timedelta(seconds=61)
        await c.get(LINKS, headers=key_header(key))
        assert await last_used() == t0 + timedelta(seconds=61)  # stale: written again


async def test_a_failed_attempt_never_updates_last_used(
    browser: Browser, anonymous: AsyncClient, db: AsyncSession
) -> None:
    good = (await new_key(browser))["key"]
    await anonymous.get(LINKS, headers=key_header(good[:-3] + "AAA"))
    assert (await db.execute(select(ApiKey.last_used_at))).scalar_one() is None


# --- password change does not revoke keys -----------------------------------------------------


async def test_changing_the_password_does_not_revoke_api_keys(
    browser: Browser, anonymous: AsyncClient
) -> None:
    key = key_header((await new_key(browser))["key"])
    resp = await browser.post(
        "/api/v1/auth/change-password",
        json={"old_password": PASSWORD, "new_password": "a brand new password"},
    )
    assert resp.status_code == 204
    assert (await anonymous.get(LINKS, headers=key)).status_code == 200  # explicit decision


# --- the per-user create limiter --------------------------------------------------------------


async def test_the_create_limit_is_per_user_and_shared_by_session_and_keys(
    alice: User, bob: User
) -> None:
    settings = settings_with(rate_limit_create_per_min=3)
    body = {"destination_url": "https://example.com/x"}
    async with running_app(settings) as (_, a), running_app(settings) as (_, b):
        alice_b = await Browser(a).login("alice@example.com")
        key1 = key_header((await new_key(alice_b, "one"))["key"])
        key2 = key_header((await new_key(alice_b, "two"))["key"])

        assert (await alice_b.post(LINKS, json=body)).status_code == 201  # session
        assert (await a.post(LINKS, json=body, headers=key1)).status_code == 201  # key one
        assert (await a.post(LINKS, json=body, headers=key2)).status_code == 201  # key two
        # Budget of 3 is spent. Another key does not buy more, and neither does the session.
        for resp in (
            await alice_b.post(LINKS, json=body),
            await a.post(LINKS, json=body, headers=key1),
            await a.post(LINKS, json=body, headers=key2),
        ):
            assert resp.status_code == 429
            assert resp.json()["error"]["code"] == "rate_limited"
            assert int(resp.headers["retry-after"]) > 0

        # Reads are not limited, and another user has their own budget.
        assert (await alice_b.get(LINKS)).status_code == 200
        bob_b = await Browser(b).login("bob@example.com")
        assert (await bob_b.post(LINKS, json=body)).status_code == 201


async def test_creating_links_fails_closed_when_redis_is_down_but_redirects_and_key_reads_work(
    browser: Browser, alice: User, db: AsyncSession
) -> None:
    key = (await new_key(browser))["key"]
    await make_link(db, alice, "live001", destination_url="https://example.com/live")
    async with running_app(settings_with(redis_url=DEAD_REDIS)) as (_, c):
        create = await c.post(
            LINKS, json={"destination_url": "https://example.com/n"}, headers=key_header(key)
        )
        read = await c.get(LINKS, headers=key_header(key))  # keys live in Postgres, not Redis
        redirect = await c.get("/live001")  # redirects fail open
    assert create.status_code == 503 and create.json()["error"]["code"] == "unavailable"
    assert read.status_code == 200 and read.json()["total"] == 1
    assert (redirect.status_code, redirect.headers["location"]) == (302, "https://example.com/live")
    assert (await db.execute(select(Link.code).where(Link.code != "live001"))).first() is None
