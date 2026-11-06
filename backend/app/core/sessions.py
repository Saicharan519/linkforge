"""Server-side sessions in Redis (ARCHITECTURE §2.4, §5).

The browser only holds a random session id; everything else lives in Redis:
  `sess:{sid}`            JSON {user_id, created_at, last_seen, ua}, expires after SESSION_TTL
  `user_sessions:{uid}`   set of that user's session ids (so "revoke the others" is possible)

Why not JWT: a server-side session can be revoked instantly (logout, password change). The price
is a Redis dependency: if Redis loses its data, everyone has to log in again.

Redis failure policy: creating a session FAILS CLOSED (no session, so no login). Reading one when
Redis is down is reported as "unavailable" (503), never as "not logged in" (401): a Redis blip
must not log users out or clear their cookies.
"""

import json
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.errors import ApiError
from app.core.redis import SafeRedis

logger = logging.getLogger(__name__)

# token_urlsafe(32) is always 43 characters from this alphabet. Checking the shape before the id
# goes into a Redis key stops a crafted cookie from addressing any other key.
SID_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")

# The sliding expiry is refreshed at most once a minute, not on every request.
TOUCH_INTERVAL = timedelta(seconds=60)

USER_AGENT_MAX = 256

_FAILED = object()


@dataclass(frozen=True, slots=True)
class Session:
    sid: str
    user_id: uuid.UUID
    last_seen: datetime


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def _sess_key(sid: str) -> str:
    return f"sess:{sid}"


def _index_key(user_id: uuid.UUID | str) -> str:
    return f"user_sessions:{user_id}"


class SessionStore:
    def __init__(self, redis: SafeRedis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def create(self, user_id: uuid.UUID, user_agent: str, now: datetime) -> str:
        """Store a new session and return its id. Raises 503 if Redis cannot store it."""
        sid = new_session_id()
        payload = json.dumps(
            {
                "user_id": str(user_id),
                "created_at": now.isoformat(),
                "last_seen": now.isoformat(),
                "ua": user_agent[:USER_AGENT_MAX],
            }
        )
        pipe = self._redis.client.pipeline(transaction=True)  # all three commands or none
        pipe.set(_sess_key(sid), payload, ex=self._ttl)
        pipe.sadd(_index_key(user_id), sid)
        pipe.expire(_index_key(user_id), self._ttl)
        if await self._redis.call("session_create", pipe.execute(), _FAILED) is _FAILED:
            raise ApiError(503, "unavailable", "the service is temporarily unavailable")
        return sid

    async def load(self, sid: str, now: datetime) -> Session | None | object:
        """The session, None if it does not exist or is unreadable, or `_FAILED`-like object if
        Redis failed (check with `store.failed(result)`)."""
        if not SID_PATTERN.fullmatch(sid):
            return None
        raw = await self._redis.call("session_get", self._redis.client.get(_sess_key(sid)), _FAILED)
        if raw is _FAILED:
            return _FAILED
        if raw is None:
            return None
        try:
            data = json.loads(raw)  # type: ignore[arg-type]  # decode_responses=True gives str
            session = Session(
                sid=sid,
                user_id=uuid.UUID(data["user_id"]),
                last_seen=datetime.fromisoformat(data["last_seen"]),
            )
        except (ValueError, KeyError, TypeError):
            # Not something we wrote. Remove it so it cannot keep failing, and report "no session".
            logger.warning("session: unreadable entry removed")
            await self._redis.call("session_destroy", self._redis.client.delete(_sess_key(sid)), 0)
            return None

        if now - session.last_seen >= TOUCH_INTERVAL:
            await self._touch(session, data, now)
        return session

    async def _touch(self, session: Session, data: dict[str, str], now: datetime) -> None:
        """Slide the expiry forward. `xx=True`: only if the session still exists, so a session
        that logout deleted a moment ago is not brought back to life."""
        data["last_seen"] = now.isoformat()
        await self._redis.call(
            "session_touch",
            self._redis.client.set(_sess_key(session.sid), json.dumps(data), ex=self._ttl, xx=True),
            None,
        )
        await self._redis.call(
            "session_touch", self._redis.client.expire(_index_key(session.user_id), self._ttl), None
        )

    async def destroy(self, sid: str, user_id: uuid.UUID) -> bool:
        """Delete the session. False if Redis failed (the caller decides what that means)."""
        if not SID_PATTERN.fullmatch(sid):
            return True  # nothing to delete: such an id can never have been stored
        pipe = self._redis.client.pipeline(transaction=True)
        pipe.delete(_sess_key(sid))
        pipe.srem(_index_key(user_id), sid)
        return await self._redis.call("session_destroy", pipe.execute(), _FAILED) is not _FAILED

    async def revoke_others(self, user_id: uuid.UUID, keep_sid: str) -> bool:
        """Delete every session of `user_id` except `keep_sid` (used by change-password).

        The index set can hold ids whose session already expired; they are removed here too, which
        is the lazy cleanup ARCHITECTURE section 2.4 describes. False if Redis failed.
        """
        index = _index_key(user_id)
        members = await self._redis.call(
            "session_revoke", self._redis.client.smembers(index), _FAILED
        )
        if members is _FAILED:
            return False
        others = [
            sid
            for sid in members  # type: ignore[attr-defined]
            if sid != keep_sid and SID_PATTERN.fullmatch(sid)
        ]
        if not others:
            return True
        pipe = self._redis.client.pipeline(transaction=True)
        pipe.delete(*[_sess_key(sid) for sid in others])
        pipe.srem(index, *others)
        return await self._redis.call("session_revoke", pipe.execute(), _FAILED) is not _FAILED

    @staticmethod
    def failed(result: object) -> bool:
        return result is _FAILED
