"""A database connection that dies while a request is finishing must not turn the answer into a 500.

Found in P8b: `kubectl delete pod postgres-0 --grace-period=0` under traffic produced ONE login
500 at the instant of the kill. The API log showed an unhandled error from `rollback` while the
request's session was being closed, on a connection Postgres had already torn down. The handler
had done its job (or had already decided on a 503); the CLOSE of the session then raised and
replaced the response.

We cannot kill Postgres inside a pytest run, so the close is made to fail the way the real one
did: after the connection went back to the pool, closing the session raises a connection error.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def close_always_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    original_close = AsyncSession.close

    async def broken_close(self: AsyncSession) -> None:
        await original_close(self)  # the connection really goes back to the pool...
        raise ConnectionResetError(
            "connection was closed in the middle of rollback"
        )  # ...then fails

    monkeypatch.setattr(AsyncSession, "close", broken_close)


async def test_a_failing_session_close_does_not_replace_a_login_answer(
    client: AsyncClient, close_always_fails: None
) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"}
    )
    assert resp.status_code == 401  # the credentials answer, not a 500


async def test_a_failing_session_close_does_not_replace_a_redirect_answer(
    client: AsyncClient, close_always_fails: None
) -> None:
    resp = await client.get("/zzzzzzz")
    assert resp.status_code == 404  # unknown code, decided by a real database lookup


async def test_the_api_keeps_serving_after_a_failed_close(
    client: AsyncClient, close_always_fails: None
) -> None:
    # More requests than the pool has connections: nothing may be leaked or stuck.
    for _ in range(8):
        assert (await client.get("/zzzzzzz")).status_code == 404
