"""CSRF token + Origin checks and change-password (task P2.5, ARCHITECTURE §2.4). Real services."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import redis.asyncio as aioredis
from argon2 import PasswordHasher
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.sessions import SessionStore, new_session_id
from app.models import User
from tests.conftest import TEST_DATABASE_URL, TEST_REDIS_URL
from tests.integration.conftest import running_app
from tests.integration.factories import make_user

PASSWORD = "correct horse battery"
NEW_PASSWORD = "a brand new password"
PASSWORD_HASH = PasswordHasher().hash(PASSWORD)
ORIGIN = "https://app.example.com"
EVIL = "https://evil.example"

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
CHANGE = "/api/v1/auth/change-password"


def settings_with(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": TEST_DATABASE_URL,
        "allowed_origins": ORIGIN,
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
async def user(db: AsyncSession) -> User:
    return await make_user(db, "alice@example.com", password_hash=PASSWORD_HASH)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with running_app(settings_with()) as (_, c):
        yield c


@pytest.fixture
async def raw() -> AsyncIterator[aioredis.Redis]:
    r = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield r
    await r.aclose()


async def login(client: AsyncClient, password: str = PASSWORD) -> None:
    resp = await client.post(LOGIN, json={"email": "alice@example.com", "password": password})
    assert resp.status_code == 200, resp.text


def good(client: AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["lf_csrf"], "Origin": ORIGIN}


# --- the token ---------------------------------------------------------------------------------


async def test_unsafe_request_with_token_and_origin_is_accepted(
    client: AsyncClient, user: User
) -> None:
    await login(client)
    assert (await client.post(LOGOUT, headers=good(client))).status_code == 204


async def test_missing_csrf_token_is_403_and_the_session_survives(
    client: AsyncClient, user: User
) -> None:
    await login(client)
    resp = await client.post(LOGOUT, headers={"Origin": ORIGIN})  # no X-CSRF-Token
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_failed"
    assert (await client.get("/api/v1/auth/me")).status_code == 200  # nothing was logged out


@pytest.mark.parametrize("bad", ["", "wrong-token", "x" * 43, "A" * 200])
async def test_wrong_csrf_token_is_403(client: AsyncClient, user: User, bad: str) -> None:
    await login(client)
    resp = await client.post(LOGOUT, headers={"X-CSRF-Token": bad, "Origin": ORIGIN})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_failed"


async def test_csrf_header_without_the_cookie_is_403(client: AsyncClient, user: User) -> None:
    """An attacker page can set a header but cannot make the browser send OUR cookie value, and
    cannot read it: a header that no cookie matches is refused even if it looks plausible."""
    await login(client)
    token = client.cookies["lf_csrf"]
    cookies = {c.name: c.value for c in client.cookies.jar if c.name != "lf_csrf"}
    resp = await client.post(
        LOGOUT, headers={"X-CSRF-Token": token, "Origin": ORIGIN, "Cookie": _cookie_header(cookies)}
    )
    assert resp.status_code == 403


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


async def test_safe_methods_need_no_token_or_origin(client: AsyncClient, user: User) -> None:
    await login(client)
    assert (await client.get("/api/v1/auth/me")).status_code == 200  # no headers at all


# --- the Origin check on session-authenticated requests (strict) ------------------------------


async def test_disallowed_origin_is_403_even_with_a_valid_token(
    client: AsyncClient, user: User
) -> None:
    await login(client)
    resp = await client.post(
        LOGOUT, headers={"X-CSRF-Token": client.cookies["lf_csrf"], "Origin": EVIL}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_failed"


@pytest.mark.parametrize(
    "origin", ["null", "https://app.example.com.evil.example", "", "http://app.example.com"]
)
async def test_lookalike_origins_are_refused(client: AsyncClient, user: User, origin: str) -> None:
    await login(client)
    resp = await client.post(
        LOGOUT, headers={"X-CSRF-Token": client.cookies["lf_csrf"], "Origin": origin}
    )
    assert resp.status_code == 403  # exact match only: scheme and host both count


@pytest.mark.parametrize("origin", [ORIGIN + "/", ORIGIN.upper().replace("HTTPS", "https")])
async def test_origin_comparison_ignores_case_and_a_trailing_slash(
    client: AsyncClient, user: User, origin: str
) -> None:
    await login(client)
    resp = await client.post(
        LOGOUT, headers={"X-CSRF-Token": client.cookies["lf_csrf"], "Origin": origin}
    )
    assert resp.status_code == 204


async def test_no_origin_requires_a_same_site_fetch_metadata(
    client: AsyncClient, user: User
) -> None:
    await login(client)
    token = {"X-CSRF-Token": client.cookies["lf_csrf"]}
    # Neither header: not provably ours, so refused (a script should use an API key).
    assert (await client.post(LOGOUT, headers=token)).status_code == 403
    for site in ("cross-site", "same-site"):
        resp = await client.post(LOGOUT, headers={**token, "Sec-Fetch-Site": site})
        assert resp.status_code == 403, site
    # The browser says the request came from our own page (or from the address bar).
    assert (
        await client.post(LOGOUT, headers={**token, "Sec-Fetch-Site": "same-origin"})
    ).status_code == 204


async def test_nothing_is_allowed_when_allowed_origins_is_empty(user: User) -> None:
    """A forgotten ALLOWED_ORIGINS must fail closed, not open."""
    async with running_app(settings_with(allowed_origins="")) as (_, c):
        await login(c)
        resp = await c.post(
            LOGOUT, headers={"X-CSRF-Token": c.cookies["lf_csrf"], "Origin": ORIGIN}
        )
    assert resp.status_code == 403


async def test_several_allowed_origins(user: User) -> None:
    settings = settings_with(allowed_origins=f" {ORIGIN} , http://localhost:5173/ ")
    async with running_app(settings) as (_, c):
        await login(c)
        resp = await c.post(
            CHANGE,
            headers={"X-CSRF-Token": c.cookies["lf_csrf"], "Origin": "http://localhost:5173"},
            json={"old_password": "wrong", "new_password": NEW_PASSWORD},
        )
    assert resp.status_code == 422  # got past CSRF and Origin; the old password was wrong


# --- Origin on register and login (D25: no token yet, but still no cross-site login) ----------


@pytest.mark.parametrize("path", ["/api/v1/auth/register", LOGIN])
async def test_register_and_login_refuse_a_foreign_origin(client: AsyncClient, path: str) -> None:
    resp = await client.post(
        path, json={"email": "a@example.com", "password": PASSWORD}, headers={"Origin": EVIL}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_failed"


async def test_login_refuses_a_cross_site_browser_request_without_origin(
    client: AsyncClient, user: User
) -> None:
    resp = await client.post(
        LOGIN,
        json={"email": "alice@example.com", "password": PASSWORD},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert resp.status_code == 403
    assert "lf_session" not in client.cookies


async def test_login_still_works_for_non_browser_clients_and_the_real_frontend(
    user: User,
) -> None:
    body = {"email": "alice@example.com", "password": PASSWORD}
    async with running_app(settings_with()) as (_, c):
        assert (await c.post(LOGIN, json=body)).status_code == 200  # curl, k6: neither header
    async with running_app(settings_with()) as (_, c):
        assert (await c.post(LOGIN, json=body, headers={"Origin": ORIGIN})).status_code == 200
    async with running_app(settings_with()) as (_, c):
        same = {"Sec-Fetch-Site": "same-origin"}
        assert (await c.post(LOGIN, json=body, headers=same)).status_code == 200


# --- change password --------------------------------------------------------------------------


async def test_change_password_switches_the_password_and_keeps_this_session(
    client: AsyncClient, user: User
) -> None:
    await login(client)
    resp = await client.post(
        CHANGE,
        headers=good(client),
        json={"old_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 204
    assert resp.content == b""
    assert (await client.get("/api/v1/auth/me")).status_code == 200  # still logged in here

    other = {"email": "alice@example.com"}
    old = await client.post(LOGIN, json={**other, "password": PASSWORD})
    new = await client.post(LOGIN, json={**other, "password": NEW_PASSWORD})
    assert (old.status_code, new.status_code) == (401, 200)


async def test_change_password_ends_every_other_session_but_not_this_one(
    client: AsyncClient, user: User, raw: aioredis.Redis
) -> None:
    await login(client)
    mine = client.cookies["lf_session"]
    async with running_app(settings_with()) as (_, laptop):
        await login(laptop)
        other_sid = laptop.cookies["lf_session"]
        assert await raw.scard(f"user_sessions:{user.id}") == 2

        resp = await client.post(
            CHANGE,
            headers=good(client),
            json={"old_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        assert resp.status_code == 204

        assert (await laptop.get("/api/v1/auth/me")).status_code == 401  # the other device is out
        assert await raw.exists(f"sess:{other_sid}") == 0
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    assert await raw.smembers(f"user_sessions:{user.id}") == {mine}


async def test_change_password_cleans_stale_and_malformed_index_entries(
    client: AsyncClient, user: User, raw: aioredis.Redis
) -> None:
    await login(client)
    stale = new_session_id()  # listed in the index but its session already expired
    await raw.sadd(f"user_sessions:{user.id}", stale, "not a session id", "../x")
    await raw.set("sess:../x", "must-survive")  # a malformed member must never address a key

    resp = await client.post(
        CHANGE, headers=good(client), json={"old_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert resp.status_code == 204
    members = await raw.smembers(f"user_sessions:{user.id}")
    assert stale not in members
    assert await raw.get("sess:../x") == "must-survive"


async def test_wrong_old_password_is_422_not_401_and_changes_nothing(
    client: AsyncClient, user: User, db: AsyncSession
) -> None:
    await login(client)
    uid = user.id  # read now: the rollback below expires the ORM object
    before = (await db.execute(select(User.password_hash).where(User.id == uid))).scalar_one()
    resp = await client.post(
        CHANGE,
        headers=good(client),
        json={"old_password": "not my password", "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == [{"field": "old_password", "message": "incorrect"}]
    assert "lf_session" not in {n for n in resp.headers.get_list("set-cookie")}  # nothing cleared
    assert (await client.get("/api/v1/auth/me")).status_code == 200  # NOT logged out
    await db.rollback()
    after = (await db.execute(select(User.password_hash).where(User.id == uid))).scalar_one()
    assert after == before


@pytest.mark.parametrize("new", ["123456789", "x" * 129, ""])
async def test_new_password_must_be_10_to_128_characters(
    client: AsyncClient, user: User, new: str
) -> None:
    await login(client)
    resp = await client.post(
        CHANGE, headers=good(client), json={"old_password": PASSWORD, "new_password": new}
    )
    assert resp.status_code == 422
    assert "new_password" in {d["field"] for d in resp.json()["error"]["details"]}
    assert new not in resp.text or new == ""


async def test_change_password_needs_the_csrf_token_and_a_session(
    client: AsyncClient, user: User, db: AsyncSession
) -> None:
    body = {"old_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert (await client.post(CHANGE, json=body)).status_code == 401  # not logged in

    await login(client)
    assert (await client.post(CHANGE, json=body)).status_code == 403  # logged in, no token/Origin
    evil = await client.post(
        CHANGE, json=body, headers={"X-CSRF-Token": client.cookies["lf_csrf"], "Origin": EVIL}
    )
    assert evil.status_code == 403
    # The password is unchanged after all those refusals.
    again = await client.post(LOGIN, json={"email": "alice@example.com", "password": PASSWORD})
    assert again.status_code == 200


async def test_password_change_that_cannot_revoke_sessions_is_reported_as_503(
    user: User, monkeypatch: pytest.MonkeyPatch, db: AsyncSession
) -> None:
    """Redis failing at the revoke step must not be reported as success (an old session could
    still be alive). The password itself IS already changed, so the user can simply retry."""

    async def failing(self: SessionStore, user_id: object, keep_sid: str) -> bool:
        return False

    async with running_app(settings_with()) as (_, c):
        await login(c)
        monkeypatch.setattr(SessionStore, "revoke_others", failing)
        resp = await c.post(
            CHANGE, headers=good(c), json={"old_password": PASSWORD, "new_password": NEW_PASSWORD}
        )
    assert resp.status_code == 503
    login_new = {"email": "alice@example.com", "password": NEW_PASSWORD}
    async with running_app(settings_with()) as (_, c2):
        assert (await c2.post(LOGIN, json=login_new)).status_code == 200  # already changed


async def test_session_store_revoke_others_unit(user: User, raw: aioredis.Redis) -> None:
    async with running_app(settings_with()) as (app, _):
        store: SessionStore = app.state.sessions
        now = datetime.now(UTC)
        keep = await store.create(user.id, "ua", now)
        gone = await store.create(user.id, "ua", now)
        assert await store.revoke_others(user.id, keep) is True
        assert await raw.exists(f"sess:{keep}") == 1
        assert await raw.exists(f"sess:{gone}") == 0
        assert await store.revoke_others(user.id, keep) is True  # nothing left to revoke: fine
