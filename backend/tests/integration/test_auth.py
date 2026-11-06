"""Register, login, logout, /me and sessions (task P2.4, ARCHITECTURE §2.4, §6.1).

Real app, real Postgres, real Redis (db 15). CSRF and Origin checks arrive in P2.5.
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import redis.asyncio as aioredis
from argon2 import PasswordHasher
from httpx import AsyncClient, Response
from prometheus_client import REGISTRY
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.sessions import SessionStore, new_session_id
from app.models import User
from tests.conftest import TEST_DATABASE_URL, TEST_REDIS_URL
from tests.integration.conftest import running_app
from tests.integration.factories import make_user

PASSWORD = "correct horse battery"
DEAD_REDIS = "redis://127.0.0.1:1/0"
DEAD_DB = "postgresql+asyncpg://nobody:nopass@127.0.0.1:1/none"
# Hashed ONCE for the whole module: creating users directly is much faster than registering
# through the API in every test (argon2 is deliberately slow).
PASSWORD_HASH = PasswordHasher().hash(PASSWORD)

LOGIN = "/api/v1/auth/login"
ORIGIN = "https://app.example.com"  # the one allowed origin in these tests


def settings_with(**overrides: object) -> Settings:
    values: dict[str, object] = {"database_url": TEST_DATABASE_URL, **overrides}
    return Settings(**values)  # type: ignore[arg-type]


def rejections(scope: str) -> float:
    return REGISTRY.get_sample_value("rate_limit_rejections_total", {"scope": scope}) or 0.0


@pytest.fixture
async def user(db: AsyncSession) -> User:
    return await make_user(db, "alice@example.com", password_hash=PASSWORD_HASH)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:  # shadows the default: same app, fresh cookies
    async with running_app(settings_with(allowed_origins=ORIGIN)) as (_, c):
        yield c


@pytest.fixture
async def raw() -> AsyncIterator[aioredis.Redis]:
    r = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield r
    await r.aclose()


async def login(
    client: AsyncClient, email: str = "alice@example.com", password: str = PASSWORD
) -> Response:
    return await client.post(LOGIN, json={"email": email, "password": password})


def plant(client: AsyncClient, name: str, value: str) -> None:
    """Put a cookie in the client exactly where the server-set ones live (the test app is served
    as host test.local), so it REPLACES the existing cookie instead of adding a second one."""
    client.cookies.set(name, value, domain="test.local", path="/")


def unsafe(client: AsyncClient) -> dict[str, str]:
    """What our own frontend sends on a state-changing request: the CSRF token and the Origin."""
    return {"X-CSRF-Token": client.cookies["lf_csrf"], "Origin": ORIGIN}


def set_cookie_headers(resp: Response) -> dict[str, str]:
    """name -> the full Set-Cookie header, for every cookie the response sets."""
    out: dict[str, str] = {}
    for header in resp.headers.get_list("set-cookie"):
        out[header.split("=", 1)[0]] = header
    return out


def is_cleared(header: str) -> bool:
    return "max-age=0" in header.lower() or "lf_" in header and '=""' in header


# --- register ---------------------------------------------------------------------------------


async def test_register_creates_an_account_with_an_argon2id_hash(
    client: AsyncClient, db: AsyncSession
) -> None:
    resp = await client.post(
        "/api/v1/auth/register", json={"email": "  New.User@Example.COM ", "password": PASSWORD}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"id", "email", "created_at"}  # never the password or its hash
    assert body["email"] == "new.user@example.com"  # trimmed and lowercased
    assert PASSWORD not in resp.text and "argon2" not in resp.text
    assert not set_cookie_headers(resp)  # registering does not log you in

    stored = (
        await db.execute(select(User.password_hash).where(User.id == body["id"]))
    ).scalar_one()
    assert stored.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


async def test_registered_user_can_log_in(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register", json={"email": "bob@example.com", "password": PASSWORD}
    )
    assert (await login(client, "bob@example.com")).status_code == 200


async def test_duplicate_email_is_409_even_with_different_case(
    client: AsyncClient, user: User
) -> None:
    resp = await client.post(
        "/api/v1/auth/register", json={"email": "ALICE@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "email_taken"


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"email": "not-an-email", "password": PASSWORD}, "email"),
        ({"email": "ünï@example.com", "password": PASSWORD}, "email"),
        ({"email": "a@example.com", "password": "123456789"}, "password"),  # 9 characters
        ({"email": "a@example.com", "password": "x" * 129}, "password"),
        ({"email": "a@example.com"}, "password"),
        ({"password": PASSWORD}, "email"),
        ({"email": "a@example.com", "password": PASSWORD, "role": "admin"}, "role"),
    ],
)
async def test_register_validation_errors_name_the_field(
    client: AsyncClient, payload: dict[str, str], field: str
) -> None:
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 422
    assert field in {d["field"] for d in resp.json()["error"]["details"]}
    assert PASSWORD not in resp.text


@pytest.mark.parametrize("length", [10, 128])
async def test_password_length_boundaries_are_accepted(client: AsyncClient, length: int) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": f"len{length}@example.com", "password": "p" * length},
    )
    assert resp.status_code == 201


# --- login ------------------------------------------------------------------------------------


async def test_login_sets_both_cookies_with_the_documented_flags(
    client: AsyncClient, user: User
) -> None:
    resp = await login(client)
    assert resp.status_code == 200
    assert resp.json()["email"] == "alice@example.com"
    cookies = set_cookie_headers(resp)
    session, csrf = cookies["lf_session"].lower(), cookies["lf_csrf"].lower()

    assert (
        "httponly" in session and "httponly" not in csrf
    )  # JS must read lf_csrf, never lf_session
    for header in (session, csrf):
        assert "samesite=lax" in header
        assert "path=/" in header
        assert "max-age=604800" in header
        assert "domain" not in header  # host-only: never sent to the short-link host
        assert "secure" not in header  # local dev over plain http


async def test_cookies_are_secure_in_production() -> None:
    # COOKIE_SECURE is on in prod. (ENV=prod refuses the dev user but real login is unaffected.)
    async with running_app(settings_with(cookie_secure=True)) as (_, c):
        # Register + login through the API: the fixture user lives in the default app's DB too.
        await c.post(
            "/api/v1/auth/register", json={"email": "sec@example.com", "password": PASSWORD}
        )
        resp = await login(c, "sec@example.com")
    cookies = set_cookie_headers(resp)
    assert "secure" in cookies["lf_session"].lower()
    assert "secure" in cookies["lf_csrf"].lower()


async def test_login_stores_the_session_in_redis(
    client: AsyncClient, user: User, raw: aioredis.Redis
) -> None:
    resp = await login(client)
    sid = client.cookies["lf_session"]
    stored = json.loads(await raw.get(f"sess:{sid}") or "{}")
    assert stored["user_id"] == str(user.id)
    assert {"created_at", "last_seen", "ua"} <= set(stored)
    assert 0 < await raw.ttl(f"sess:{sid}") <= 604800
    assert await raw.sismember(f"user_sessions:{user.id}", sid)
    assert len(sid) == 43  # 32 random bytes, url-safe
    assert resp.json()["id"] == str(user.id)


async def test_wrong_password_and_unknown_email_are_indistinguishable(
    client: AsyncClient, user: User
) -> None:
    wrong_password = await login(client, password="not the password")
    unknown_email = await login(client, "nobody@example.com", "not the password")
    malformed_email = await login(client, "###", "not the password")
    for resp in (wrong_password, unknown_email, malformed_email):
        assert resp.status_code == 401
        assert not set_cookie_headers(resp)  # no session, no cookies
    assert wrong_password.json() == unknown_email.json() == malformed_email.json()
    assert wrong_password.json()["error"]["code"] == "invalid_credentials"


async def test_login_email_is_case_insensitive(client: AsyncClient, user: User) -> None:
    assert (await login(client, "  ALICE@Example.com ")).status_code == 200


async def test_disabled_account_cannot_log_in(
    client: AsyncClient, user: User, db: AsyncSession
) -> None:
    await db.execute(update(User).where(User.id == user.id).values(is_active=False))
    await db.commit()
    assert (await login(client)).status_code == 401


async def test_login_rotates_the_session(
    client: AsyncClient, user: User, raw: aioredis.Redis
) -> None:
    await login(client)
    old = client.cookies["lf_session"]
    assert await raw.exists(f"sess:{old}") == 1

    await login(client)  # the browser still sends the old cookie
    new = client.cookies["lf_session"]
    assert new != old
    assert await raw.exists(f"sess:{old}") == 0  # destroyed, never reused
    assert not await raw.sismember(f"user_sessions:{user.id}", old)
    assert await raw.sismember(f"user_sessions:{user.id}", new)

    plant(client, "lf_session", old)  # replaying the old id gets nothing
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_a_planted_session_cookie_is_not_adopted(client: AsyncClient, user: User) -> None:
    """Session fixation: the attacker plants an id before login; it must not become the session."""
    planted = new_session_id()
    plant(client, "lf_session", planted)
    await login(client)
    assert client.cookies["lf_session"] != planted


# --- login rate limit (5 failed / 15 min, fails closed) ---------------------------------------


async def test_five_failures_then_429_even_for_the_right_password(
    client: AsyncClient, user: User
) -> None:
    before = rejections("login")
    for _ in range(5):
        assert (await login(client, password="wrong password!")).status_code == 401
    blocked = await login(client)  # correct password, but the failure budget is spent
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) > 0
    assert rejections("login") == before + 1
    # Another email is another bucket.
    other = await login(client, "carol@example.com", "wrong password!")
    assert other.status_code == 401


async def test_successful_logins_do_not_use_up_the_budget(client: AsyncClient, user: User) -> None:
    for _ in range(12):  # far more than 5
        assert (await login(client)).status_code == 200


async def test_a_success_between_failures_gives_its_own_attempt_back(
    client: AsyncClient, user: User
) -> None:
    """Only FAILURES use up the budget of 5: the success in the middle is refunded, the 4 earlier
    failures stay counted."""
    for _ in range(4):
        assert (await login(client, password="wrong password!")).status_code == 401
    assert (await login(client)).status_code == 200  # counted, then refunded
    assert (await login(client, password="wrong password!")).status_code == 401  # the 5th failure
    assert (await login(client, password="wrong password!")).status_code == 429  # budget spent


async def test_spoofed_x_forwarded_for_does_not_reset_the_login_limit(
    client: AsyncClient, user: User
) -> None:
    for i in range(5):
        resp = await client.post(
            LOGIN,
            json={"email": "alice@example.com", "password": "wrong password!"},
            headers={"X-Forwarded-For": f"8.8.8.{i}"},
        )
        assert resp.status_code == 401
    resp = await client.post(
        LOGIN,
        json={"email": "alice@example.com", "password": PASSWORD},
        headers={"X-Forwarded-For": "8.8.8.250"},
    )
    assert resp.status_code == 429


async def test_login_fails_closed_when_redis_is_down(user: User) -> None:
    async with running_app(settings_with(redis_url=DEAD_REDIS)) as (_, c):
        resp = await login(c)  # the password is right: still refused
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "unavailable"
    assert not set_cookie_headers(resp)


async def test_login_with_redis_down_never_reveals_whether_the_password_was_right(
    user: User,
) -> None:
    """If the limiter failed OPEN, wrong passwords would answer 401 and the right one 503, which is
    an unlimited password-guessing oracle while Redis is down. Fail closed: both are 503."""
    async with running_app(settings_with(redis_url=DEAD_REDIS)) as (_, c):
        wrong = await login(c, password="wrong password!")
        right = await login(c)
    assert wrong.status_code == right.status_code == 503
    assert wrong.json() == right.json()


async def test_session_ids_with_the_wrong_shape_never_reach_redis() -> None:
    # With Redis down a real lookup would report a FAILURE; a malformed id is answered "no such
    # session" without asking Redis at all.
    async with running_app(settings_with(redis_url=DEAD_REDIS)) as (app, _):
        store: SessionStore = app.state.sessions
        now = datetime.now(UTC)
        assert await store.load("../../etc/passwd", now) is None
        assert await store.load("short", now) is None
        assert store.failed(await store.load(new_session_id(), now))  # well-formed: asks Redis


async def test_login_is_503_when_postgres_is_down() -> None:
    async with running_app(settings_with(database_url=DEAD_DB)) as (_, c):
        resp = await login(c)
    assert resp.status_code == 503


async def test_passwords_never_reach_the_logs(
    client: AsyncClient, user: User, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "an-unmistakable-secret-password"
    with caplog.at_level(logging.DEBUG):
        await login(client, password=secret)
        await login(client)
        await client.post("/api/v1/auth/register", json={"email": "bad", "password": secret})
    assert secret not in caplog.text and PASSWORD not in caplog.text


# --- /me and session validation ---------------------------------------------------------------


async def test_me_returns_the_logged_in_user(client: AsyncClient, user: User) -> None:
    await login(client)
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(user.id)
    assert resp.json()["email"] == "alice@example.com"


async def test_me_without_a_session_is_401_and_clears_both_cookies(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    cookies = set_cookie_headers(resp)
    assert set(cookies) == {"lf_session", "lf_csrf"}
    assert all("max-age=0" in h.lower() for h in cookies.values())


@pytest.mark.parametrize(
    "value",
    [
        "short",
        "x" * 44,
        "../../etc/passwd" + "a" * 30,
        "a" * 42 + "*",
        "a" * 42 + " ",
        "'; DROP TABLE users;--" + "a" * 30,
    ],
)
async def test_malformed_session_cookies_are_401_and_never_reach_redis(
    client: AsyncClient, raw: aioredis.Redis, value: str
) -> None:
    resp = await client.get("/api/v1/auth/me", headers={"Cookie": f"lf_session={value}"})
    assert resp.status_code == 401
    assert set(set_cookie_headers(resp)) == {"lf_session", "lf_csrf"}
    assert await raw.keys("sess:*") == []


async def test_unknown_and_expired_sessions_are_401_with_cleared_cookies(
    client: AsyncClient, user: User, raw: aioredis.Redis
) -> None:
    # Well-formed but never stored.
    plant(client, "lf_session", new_session_id())
    assert (await client.get("/api/v1/auth/me")).status_code == 401

    # Stored, then expired (Redis removed it).
    await login(client)
    await raw.delete(f"sess:{client.cookies['lf_session']}")
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert set(set_cookie_headers(resp)) == {"lf_session", "lf_csrf"}


async def test_an_unreadable_session_entry_is_401_and_is_removed(
    client: AsyncClient, raw: aioredis.Redis
) -> None:
    sid = new_session_id()
    await raw.set(f"sess:{sid}", "{ this is not json", ex=60)
    plant(client, "lf_session", sid)
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert await raw.exists(f"sess:{sid}") == 0


async def test_a_session_of_a_deleted_user_is_401_and_is_destroyed(
    client: AsyncClient, user: User, db: AsyncSession, raw: aioredis.Redis
) -> None:
    await login(client)
    sid = client.cookies["lf_session"]
    await db.execute(update(User).where(User.id == user.id).values(is_active=False))
    await db.commit()
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert await raw.exists(f"sess:{sid}") == 0  # does not outlive the account


async def test_redis_down_is_503_not_401_and_cookies_are_kept(user: User) -> None:
    async with running_app(settings_with(redis_url=DEAD_REDIS)) as (_, c):
        plant(c, "lf_session", new_session_id())
        resp = await c.get("/api/v1/auth/me")
    assert resp.status_code == 503  # an outage must not log people out
    assert not set_cookie_headers(resp)


# --- sliding expiry ---------------------------------------------------------------------------


async def test_session_expiry_slides_at_most_once_a_minute(
    client: AsyncClient, user: User, raw: aioredis.Redis
) -> None:
    await login(client)
    sid = client.cookies["lf_session"]
    key = f"sess:{sid}"

    # Used just now: not rewritten, TTL is left alone.
    await raw.expire(key, 1000)
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    assert 0 < await raw.ttl(key) <= 1000

    # Last seen 5 minutes ago: refreshed to the full lifetime and last_seen moves forward.
    data = json.loads(await raw.get(key) or "{}")
    data["last_seen"] = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await raw.set(key, json.dumps(data), ex=1000)
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    assert await raw.ttl(key) > 604000
    refreshed = json.loads(await raw.get(key) or "{}")
    assert datetime.fromisoformat(refreshed["last_seen"]) > datetime.now(UTC) - timedelta(minutes=1)


async def test_touch_cannot_bring_back_a_deleted_session(user: User, raw: aioredis.Redis) -> None:
    async with running_app(settings_with()) as (app, _):
        store: SessionStore = app.state.sessions
        sid = await store.create(user.id, "ua", datetime.now(UTC))
        session = await store.load(sid, datetime.now(UTC))
        await raw.delete(f"sess:{sid}")  # logout happens between load and touch
        await store._touch(session, {"user_id": str(user.id)}, datetime.now(UTC))  # type: ignore[arg-type]
    assert await raw.exists(f"sess:{sid}") == 0


# --- logout -----------------------------------------------------------------------------------


async def test_logout_destroys_the_session_and_clears_cookies(
    client: AsyncClient, user: User, raw: aioredis.Redis
) -> None:
    await login(client)
    sid = client.cookies["lf_session"]
    resp = await client.post("/api/v1/auth/logout", headers=unsafe(client))
    assert resp.status_code == 204
    assert resp.content == b""
    assert set(set_cookie_headers(resp)) == {"lf_session", "lf_csrf"}
    assert await raw.exists(f"sess:{sid}") == 0
    assert not await raw.sismember(f"user_sessions:{user.id}", sid)

    plant(client, "lf_session", sid)  # the old cookie is now worthless
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_logout_only_ends_this_session(client: AsyncClient, user: User) -> None:
    await login(client)
    first = client.cookies["lf_session"]
    async with running_app(settings_with(allowed_origins=ORIGIN)) as (_, other):
        await login(other)  # a second device
        second = other.cookies["lf_session"]
        assert first != second
        await client.post("/api/v1/auth/logout", headers=unsafe(client))
        assert (await other.get("/api/v1/auth/me")).status_code == 200


async def test_logout_without_a_session_is_401(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code == 401
    assert uuid.UUID(int=0)  # keeps the import honest; the point is the status above
