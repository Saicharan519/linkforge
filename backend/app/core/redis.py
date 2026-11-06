"""Redis access with hard time bounds and a fallback for every call (ARCHITECTURE §2.1, §5.1).

Rule for the whole app: a Redis problem NEVER becomes a 500. Every Redis operation goes through
`SafeRedis.call(op, awaitable, fallback)`. If Redis is down, slow, out of memory or answers
nonsense, the call returns `fallback` and `redis_errors_total{op=...}` goes up. The CALLER decides
what the fallback means (the redirect limiter fails OPEN, the login limiter fails CLOSED), so the
fallback is always explicit at the call site, never implied here.

Only Redis/network/timeout failures are swallowed. A bug in our own code (a TypeError, say) is
not a Redis error and must stay loud, so it propagates.
"""

import asyncio
import logging
from collections.abc import Awaitable
from typing import TypeVar

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import Settings
from app.core.metrics import REDIS_ERRORS

logger = logging.getLogger(__name__)

T = TypeVar("T")
F = TypeVar("F")


class SafeRedis:
    def __init__(self, client: Redis, deadline_seconds: float) -> None:
        self.client = client
        self._deadline = deadline_seconds

    async def call(self, op: str, awaitable: Awaitable[T], fallback: F) -> T | F:
        """Await a Redis command; on a Redis-side failure return `fallback` and count it.

        The socket timeouts on the client bound each network step. `asyncio.timeout` is a second,
        overall bound for the whole call, so nothing (a stalled connect, a stalled read) can hold
        a request longer than `deadline_seconds`.
        """
        try:
            async with asyncio.timeout(self._deadline):
                return await awaitable
        except (RedisError, OSError, TimeoutError) as exc:
            REDIS_ERRORS.labels(op=op).inc()
            # Class name only: the message can contain the Redis URL, which may hold a password.
            logger.warning("redis op=%s failed: %s", op, type(exc).__name__)
            return fallback

    async def aclose(self) -> None:
        await self.client.aclose()


def create_redis(settings: Settings) -> SafeRedis:
    """Build the client. It connects lazily, so the app boots even when Redis is down."""
    socket_timeout = settings.redis_socket_timeout_ms / 1000
    connect_timeout = settings.redis_connect_timeout_ms / 1000
    client = Redis.from_url(
        settings.redis_url,
        socket_timeout=socket_timeout,
        socket_connect_timeout=connect_timeout,
        decode_responses=True,
        # No retries inside the client: the fallback IS the retry policy, and a hidden retry
        # would silently double the time bound.
        retry_on_timeout=False,
    )
    return SafeRedis(client, deadline_seconds=socket_timeout + connect_timeout)
