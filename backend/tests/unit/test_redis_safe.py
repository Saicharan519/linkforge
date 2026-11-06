"""SafeRedis fallback behaviour that needs NO real Redis (ARCHITECTURE §2.1, task P2.1).

Two kinds of "Redis is broken" are tested, because they behave differently on the network:
- unreachable: nothing listens, the connection is refused at once;
- hung: the port accepts the connection but never answers (the dangerous one: without a time
  bound, every request would wait for it).
"""

import asyncio
import time
from collections.abc import AsyncIterator

import pytest
from prometheus_client import REGISTRY

from app.config import Settings
from app.core.redis import SafeRedis, create_redis

# Generous upper bound for "fast": the configured bound is 100 ms socket + 200 ms connect, so a
# bounded call must finish well inside a second even on a slow CI machine.
FAST = 1.0


def errors(op: str) -> float:
    return REGISTRY.get_sample_value("redis_errors_total", {"op": op}) or 0.0


def settings_for(url: str) -> Settings:
    return Settings(database_url="postgresql+asyncpg://u:p@localhost/db", redis_url=url)


@pytest.fixture
async def hung_server_url() -> AsyncIterator[str]:
    """A TCP server that accepts connections and then says nothing, ever."""
    held: list[asyncio.StreamWriter] = []

    async def handle(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)  # keep the connection open and silent

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield f"redis://127.0.0.1:{port}/0"
    for w in held:
        w.close()
    server.close()
    await server.wait_closed()


async def test_unreachable_redis_returns_fallback_without_exception() -> None:
    # Port 1: nothing listens, so the connection is refused immediately.
    r = create_redis(settings_for("redis://127.0.0.1:1/0"))
    before = errors("t_unreachable")
    started = time.monotonic()

    result = await r.call("t_unreachable", r.client.get("k"), "FALLBACK")

    assert result == "FALLBACK"
    assert time.monotonic() - started < FAST
    assert errors("t_unreachable") == before + 1
    await r.aclose()


async def test_hung_redis_is_cut_off_by_the_time_bound(hung_server_url: str) -> None:
    r = create_redis(settings_for(hung_server_url))
    before = errors("t_hung")
    started = time.monotonic()

    result = await r.call("t_hung", r.client.get("k"), None)

    assert result is None
    # Bounded by socket_timeout (100 ms), not by the OS: this would hang forever without it.
    assert time.monotonic() - started < FAST
    assert errors("t_hung") == before + 1
    await r.aclose()


async def test_overall_deadline_applies_even_if_the_client_never_times_out() -> None:
    # The belt-and-braces `asyncio.timeout`: a call that the client itself would never abandon.
    r = SafeRedis(create_redis(settings_for("redis://127.0.0.1:1/0")).client, 0.05)
    before = errors("t_deadline")

    async def stuck() -> str:
        await asyncio.sleep(30)
        return "never"

    started = time.monotonic()
    assert await r.call("t_deadline", stuck(), "FALLBACK") == "FALLBACK"
    assert time.monotonic() - started < FAST
    assert errors("t_deadline") == before + 1
    await r.aclose()


async def test_programming_errors_are_not_swallowed() -> None:
    # Only Redis/network/timeout failures use the fallback. A bug in our own code must stay loud.
    r = create_redis(settings_for("redis://127.0.0.1:1/0"))
    before = errors("t_bug")

    async def buggy() -> None:
        raise ValueError("our bug")

    with pytest.raises(ValueError):
        await r.call("t_bug", buggy(), None)
    assert errors("t_bug") == before
    await r.aclose()


def test_redis_settings_defaults_match_architecture() -> None:
    # ARCHITECTURE §9.1: 100 ms socket timeout, 200 ms connect timeout.
    s = settings_for("redis://x/0")
    assert (s.redis_socket_timeout_ms, s.redis_connect_timeout_ms) == (100, 200)
