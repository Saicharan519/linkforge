"""Fixed-window rate limiting in Redis (ARCHITECTURE §5, §2.1 step 1, §2.4).

One counter per (scope, identifier, window): `rl:{scope}:{id}:{window}`. A request increments it;
more than `limit` in one window is rejected with 429. The increment and the expiry are ONE Lua
script, so Redis runs them as a single atomic step. Without that, a crash or a second client
between `INCR` and `EXPIRE` could leave a counter with no expiry that blocks someone forever.

What happens when Redis itself fails is a per-scope POLICY, passed in by the caller:
- fail OPEN  (redirect): let the request through. A Redis outage must not take redirects down.
- fail CLOSED (login, link creation): refuse with 503. Letting these through unlimited would
  allow password guessing exactly when we can no longer count attempts.
"""

import logging
import math
from functools import cached_property

from redis.commands.core import AsyncScript

from app.core.errors import ApiError
from app.core.metrics import RATE_LIMIT_REJECTIONS
from app.core.redis import SafeRedis

logger = logging.getLogger(__name__)

# KEYS[1] = counter key, ARGV[1] = window length in seconds.
# Returns the count after the increment.
# The TTL check also heals a counter that somehow exists without an expiry.
_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 or redis.call('TTL', KEYS[1]) < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""

_FAILED = object()


class RateLimiter:
    def __init__(self, redis: SafeRedis) -> None:
        self._redis = redis

    @cached_property
    def _script(self) -> AsyncScript:
        # redis-py sends EVALSHA and transparently re-loads the script if Redis forgot it
        # (after a restart without persistence), so a flushed script cache is not an error.
        return self._redis.client.register_script(_LUA)

    async def enforce(
        self,
        scope: str,
        identifier: str,
        *,
        limit: int,
        window_seconds: int,
        now_epoch: float,
        fail_open: bool,
    ) -> bool:
        """Count this request. Raise ApiError 429 if over the limit.

        Returns True if Redis answered, False if it failed and `fail_open` let the request pass
        (so the caller can skip its own remaining Redis steps). With `fail_open=False` a Redis
        failure raises 503 instead.
        """
        window = int(now_epoch // window_seconds)
        key = f"rl:{scope}:{identifier}:{window}"
        result = await self._redis.call(
            "ratelimit", self._script(keys=[key], args=[window_seconds]), _FAILED
        )
        if result is _FAILED:
            if fail_open:
                return False
            raise ApiError(503, "unavailable", "the service is temporarily unavailable")

        if int(result) > limit:  # type: ignore[call-overload]
            RATE_LIMIT_REJECTIONS.labels(scope=scope).inc()
            raise ApiError(
                429,
                "rate_limited",
                "too many requests, slow down",
                # The counter is per fixed window, so the caller is free again when it ends.
                headers={
                    "Retry-After": str(
                        max(1, math.ceil(window_seconds - now_epoch % window_seconds))
                    )
                },
            )
        return True
