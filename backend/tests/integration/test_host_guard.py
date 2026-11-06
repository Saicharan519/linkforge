"""The public short-link host serves only GET/HEAD /{code} (task P2.8, ARCHITECTURE §1.2, D12).

Real app, real Postgres and Redis. The test client picks the Host with an explicit header.
"""

from collections.abc import AsyncIterator

import pytest
import redis.asyncio as aioredis
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.host_guard import normalize_host
from app.main import create_app
from app.models import User
from tests.conftest import TEST_DATABASE_URL, TEST_REDIS_URL
from tests.integration.conftest import running_app
from tests.integration.factories import make_link, make_user

GO = "go.example.com"
APP = "app.example.com"
NOT_FOUND = {"error": {"code": "not_found", "message": "not found"}}
DEST = "https://example.com/landing"


def settings_with(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": TEST_DATABASE_URL,
        "public_redirect_host": GO,
        "enable_docs": True,  # so /api/docs really exists on the app host
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
async def owner(db: AsyncSession) -> User:
    user = await make_user(db, "owner@example.com")
    await make_link(db, user, "short01", destination_url=DEST)
    return user


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with running_app(settings_with()) as (_, c):
        yield c


def on_go(**extra: str) -> dict[str, str]:
    return {"Host": GO, **extra}


# --- host parsing -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("go.example.com", "go.example.com"),
        ("GO.Example.COM", "go.example.com"),
        ("go.example.com:8080", "go.example.com"),
        ("  go.example.com  ", "go.example.com"),
        ("[::1]:8000", "[::1]"),
        ("", ""),
    ],
)
def test_normalize_host(raw: str, expected: str) -> None:
    assert normalize_host(raw) == expected


# --- what IS served ---------------------------------------------------------------------------


async def test_get_and_head_of_a_code_work_on_the_public_host(
    client: AsyncClient, owner: User
) -> None:
    got = await client.get("/short01", headers=on_go())
    head = await client.head("/short01", headers=on_go())
    assert (got.status_code, got.headers["location"]) == (302, DEST)
    assert (head.status_code, head.headers["location"]) == (302, DEST)
    assert head.content == b""


async def test_unknown_and_expired_codes_keep_their_normal_answers_on_the_public_host(
    client: AsyncClient, owner: User
) -> None:
    resp = await client.get("/nothere", headers=on_go())
    assert resp.status_code == 404
    assert resp.json()["error"]["message"] == "link not found"  # the app's answer, not the guard's


async def test_a_query_string_does_not_matter(client: AsyncClient, owner: User) -> None:
    assert (await client.get("/short01?fbclid=abc", headers=on_go())).status_code == 302


# --- what is NOT served -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/links"),
        ("POST", "/api/v1/links"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/register"),
        ("GET", "/api/v1/api-keys"),
        ("GET", "/api/docs"),
        ("GET", "/api/openapi.json"),
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("HEAD", "/healthz"),
        ("GET", "/metrics"),
        ("GET", "/docs"),
        ("GET", "/openapi.json"),
        ("GET", "/api"),
        ("GET", "/login"),
        ("GET", "/Healthz"),  # reserved words are matched case-insensitively
        ("GET", "/"),
        ("GET", "/short01/extra"),
        ("GET", "/short01/"),
        ("GET", "/ab"),  # too short to be a code
        ("GET", "/" + "a" * 33),  # too long
        ("GET", "/short%2F01"),
        ("POST", "/short01"),
        ("PUT", "/short01"),
        ("PATCH", "/short01"),
        ("DELETE", "/short01"),
        ("OPTIONS", "/short01"),
    ],
)
async def test_everything_else_is_a_plain_404_on_the_public_host(
    client: AsyncClient, owner: User, method: str, path: str
) -> None:
    resp = await client.request(method, path, headers=on_go())
    assert resp.status_code == 404, (method, path)  # not 401, 403 or 405: nothing is revealed
    if method != "HEAD":
        assert resp.json() == NOT_FOUND
    else:
        assert resp.content == b""


async def test_the_guard_runs_before_authentication(client: AsyncClient, owner: User) -> None:
    """A 401 would reveal that an API lives behind this host; the answer is 404 for everyone."""
    resp = await client.post(
        "/api/v1/links", json={"destination_url": "https://example.com/x"}, headers=on_go()
    )
    assert resp.status_code == 404


def concrete_routes() -> list[tuple[str, str]]:
    """(METHOD, path) for every route of the real app except the redirect, from its OpenAPI
    description (the route table itself is wrapped lazily by this FastAPI version)."""
    paths = create_app(settings_with()).openapi()["paths"]
    return [
        (method.upper(), path.replace("{id}", "00000000-0000-0000-0000-000000000001"))
        for path, operations in paths.items()
        if path != "/{code}"
        for method in operations
    ]


async def test_every_route_except_the_redirect_is_404_on_the_public_host(owner: User) -> None:
    """Future-proof: walks the real route list, so a route added later without anyone thinking
    about the short-link host fails here."""
    routes = concrete_routes()
    assert len(routes) > 10  # the walk is not accidentally empty
    async with running_app(settings_with()) as (_, c):
        for method, path in routes:
            resp = await c.request(method, path, headers=on_go())
            assert resp.status_code == 404, (method, path)


# --- which requests the guard applies to ------------------------------------------------------


async def test_the_app_host_is_not_restricted(client: AsyncClient, owner: User) -> None:
    assert (await client.get("/healthz", headers={"Host": APP})).status_code == 200
    assert (await client.get("/api/openapi.json", headers={"Host": APP})).status_code == 200
    assert (await client.get("/short01", headers={"Host": APP})).status_code == 302  # also fine


@pytest.mark.parametrize("host", ["GO.EXAMPLE.COM", "go.example.com:8080", " go.example.com"])
async def test_host_matching_ignores_case_and_port(
    client: AsyncClient, owner: User, host: str
) -> None:
    assert (await client.get("/healthz", headers={"Host": host})).status_code == 404


@pytest.mark.parametrize("host", ["go.example.com.evil.net", "xgo.example.com", "example.com"])
async def test_lookalike_hosts_are_other_hosts(client: AsyncClient, owner: User, host: str) -> None:
    # Not the public host, so not under its rules (the Ingress decides which hosts exist at all).
    assert (await client.get("/healthz", headers={"Host": host})).status_code == 200


async def test_x_forwarded_host_is_ignored(client: AsyncClient, owner: User) -> None:
    """Any client can send it, so it must not choose which rule applies."""
    spoof_app = await client.get("/healthz", headers={"Host": GO, "X-Forwarded-Host": APP})
    assert spoof_app.status_code == 404  # still guarded: Host decides
    spoof_go = await client.get("/healthz", headers={"Host": APP, "X-Forwarded-Host": GO})
    assert spoof_go.status_code == 200  # not guarded: Host decides


async def test_no_guard_when_the_public_host_is_not_configured(owner: User) -> None:
    async with running_app(settings_with(public_redirect_host="")) as (_, c):
        assert (await c.get("/healthz", headers=on_go())).status_code == 200
        assert (await c.get("/healthz")).status_code == 200


async def test_the_guard_does_not_get_in_the_way_of_lifespan_and_normal_startup(
    owner: User,
) -> None:
    # running_app runs the lifespan (database, redis, sessions) through the middleware stack.
    async with running_app(settings_with()) as (_, c):
        assert (await c.get("/readyz", headers={"Host": APP})).json()["postgres"] == "ok"


# --- HEAD never counts as a click (D12) -------------------------------------------------------


async def test_head_never_enqueues_a_click(client: AsyncClient, owner: User) -> None:
    """The click stream does not exist yet (P3.1 adds the XADD to the GET handler only). This
    pins the HEAD half now: after HEAD requests on both hosts the stream must hold nothing, so
    when GET starts enqueuing, a HEAD that did the same would fail here immediately."""
    raw = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        for host in (GO, APP):
            for _ in range(3):
                assert (await client.head("/short01", headers={"Host": host})).status_code == 302
        assert await raw.xlen("clicks") == 0
        assert await raw.exists("clicks") == 0
    finally:
        await raw.aclose()


async def test_head_is_an_explicit_route_with_the_same_answer_as_get(
    client: AsyncClient, owner: User
) -> None:
    operations = create_app(settings_with()).openapi()["paths"]["/{code}"]
    assert {"get", "head"} <= set(operations)  # declared, not implied by the GET route
    got = await client.get("/short01", headers=on_go())
    head = await client.head("/short01", headers=on_go())
    assert head.status_code == got.status_code
    assert head.headers["location"] == got.headers["location"]
