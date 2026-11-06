"""Shared FastAPI dependencies."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.client_ip import client_ip
from app.core.cookies import SESSION_COOKIE
from app.core.csrf import check_origin, protect_cookie_request
from app.core.errors import ApiError
from app.core.passwords import PasswordService
from app.core.rate_limit import RateLimiter
from app.core.redis import SafeRedis
from app.core.sessions import SID_PATTERN, Session, SessionStore
from app.db import get_db
from app.models import User


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_redis(request: Request) -> SafeRedis:
    return request.app.state.redis


def get_limiter(request: Request) -> RateLimiter:
    return request.app.state.limiter


def get_sessions(request: Request) -> SessionStore:
    return request.app.state.sessions


def get_passwords(request: Request) -> PasswordService:
    return request.app.state.passwords


def get_client_ip(request: Request) -> str:
    return client_ip(request, request.app.state.settings.trusted_proxy_count)


def get_now() -> datetime:
    """The current UTC time. A dependency so tests can pin the clock (boundary cases)."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: uuid.UUID
    email: str


DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
Now = Annotated[datetime, Depends(get_now)]
RedisClient = Annotated[SafeRedis, Depends(get_redis)]
Limiter = Annotated[RateLimiter, Depends(get_limiter)]
ClientIp = Annotated[str, Depends(get_client_ip)]
Sessions = Annotated[SessionStore, Depends(get_sessions)]
Passwords = Annotated[PasswordService, Depends(get_passwords)]

# ------------------------------------------------------------------------------------------
# TEMPORARY (decision D37): there is no authentication yet (P2). Until then every request acts
# as one fixed development user. P2.6 replaces this whole dependency with real session/API-key
# auth; `test_dev_user_is_temporary` fails the moment real auth lands so it cannot be forgotten.
# ------------------------------------------------------------------------------------------
DEV_USER_EMAIL = "dev@linkforge.local"


async def get_current_user(settings: AppSettings, db: DbSession) -> CurrentUser:
    if settings.env == "prod":
        # Never let the placeholder act as an identity in production.
        raise ApiError(401, "unauthorized", "authentication is required")

    stmt = select(User.id, User.email).where(func.lower(User.email) == DEV_USER_EMAIL)
    row = (await db.execute(stmt)).first()
    if row is None:
        # "!" is not a valid password hash, so nobody can ever log in as this user.
        await db.execute(
            pg_insert(User).values(email=DEV_USER_EMAIL, password_hash="!").on_conflict_do_nothing()
        )
        await db.commit()
        row = (await db.execute(stmt)).one()
    return CurrentUser(id=row.id, email=row.email)


AuthUser = Annotated[CurrentUser, Depends(get_current_user)]


# ------------------------------------------------------------------------------------------
# Real authentication by session cookie (P2.4). It is used by /auth/me and /auth/logout now;
# P2.6 makes it (plus API keys) the identity for every endpoint and removes the dev user above.
# ------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SessionContext:
    sid: str
    user_id: uuid.UUID
    email: str
    created_at: datetime


def _unavailable() -> ApiError:
    return ApiError(503, "unavailable", "the service is temporarily unavailable")


async def get_session_context(
    request: Request, db: DbSession, sessions: Sessions, now: Now
) -> SessionContext:
    """Who is logged in? Raises 401 (and clears both cookies) for a missing, malformed, expired,
    revoked or orphaned session, and 503 if Redis or Postgres cannot answer: an outage must not
    look like "you are logged out"."""
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid or not SID_PATTERN.fullmatch(sid):
        raise ApiError.unauthorized()

    loaded = await sessions.load(sid, now)
    if sessions.failed(loaded):
        raise _unavailable()
    if loaded is None:
        raise ApiError.unauthorized()
    session: Session = loaded  # type: ignore[assignment]  # not None, not the failure marker

    try:
        stmt = select(User.email, User.created_at, User.is_active).where(User.id == session.user_id)
        row = (await db.execute(stmt)).first()
    except (SQLAlchemyError, OSError, TimeoutError):
        raise _unavailable() from None
    if row is None or not row.is_active:
        # The account is gone or disabled: the session must not outlive it.
        await sessions.destroy(sid, session.user_id)
        raise ApiError.unauthorized()
    # A valid cookie session: for state-changing requests also require the CSRF token and Origin.
    protect_cookie_request(request, request.app.state.settings)
    return SessionContext(
        sid=sid, user_id=session.user_id, email=row.email, created_at=row.created_at
    )


SessionAuth = Annotated[SessionContext, Depends(get_session_context)]


def origin_guard(request: Request) -> None:
    """For register and login (decision D25): no cookie exists yet, so no CSRF token, but a
    browser-sent cross-site request must still be refused (login CSRF would sign the victim in to
    the attacker's account)."""
    check_origin(request, request.app.state.settings, strict=False)
