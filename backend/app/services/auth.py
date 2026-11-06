"""Registration, login and logout (ARCHITECTURE §2.4). Routers stay thin and call these."""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.emails import normalize_email
from app.core.errors import ApiError
from app.core.passwords import PasswordService
from app.core.rate_limit import RateLimiter
from app.core.sessions import SessionStore
from app.models import User

logger = logging.getLogger(__name__)

LOGIN_WINDOW_SECONDS = 15 * 60


def _bad_credentials() -> ApiError:
    # One message for "no such email", "wrong password" and "account disabled": the caller must
    # not be able to tell which emails are registered. A fresh object per call (an exception
    # instance raised again and again would keep growing its traceback).
    return ApiError(401, "invalid_credentials", "invalid email or password")


async def register(
    db: AsyncSession, passwords: PasswordService, email_raw: str, password: str
) -> User:
    email = normalize_email(email_raw)
    if email is None:
        raise ApiError.validation([("email", "must be a valid email address")])

    password_hash = await passwords.hash(password)
    user = User(email=email, password_hash=password_hash)
    try:
        # A savepoint, so a duplicate only undoes this INSERT and the session stays usable.
        async with db.begin_nested():
            db.add(user)
            await db.flush()
    except IntegrityError as exc:
        # The unique index is on lower(email): "A@x.com" and "a@x.com" are one account (PRD F1).
        if "uq_users_email_lower" in str(exc.orig):
            raise ApiError(409, "email_taken", "this email is already registered") from None
        raise
    await db.commit()
    await db.refresh(user)
    return user


def _login_identifier(ip: str, email_raw: str) -> str:
    """Limiter identity: client IP + email (PRD F1). The email is hashed so that no address is
    written into Redis key names or logs."""
    email = (normalize_email(email_raw) or email_raw.strip().lower()).encode()
    return f"{ip}:{hashlib.sha256(email).hexdigest()[:32]}"


async def login(
    db: AsyncSession,
    passwords: PasswordService,
    sessions: SessionStore,
    limiter: RateLimiter,
    settings: Settings,
    now: datetime,
    *,
    ip: str,
    email_raw: str,
    password: str,
    user_agent: str,
    previous_sid: str | None,
) -> tuple[User, str, str]:
    """Check the credentials and open a session. Returns (user, session id, csrf token)."""
    identifier = _login_identifier(ip, email_raw)
    window = dict(window_seconds=LOGIN_WINDOW_SECONDS, now_epoch=now.timestamp())

    # Count this attempt BEFORE checking the password, so parallel guesses cannot all slip in
    # ahead of the counter; a correct password gives the attempt back below. Net effect: the limit
    # is "5 FAILED attempts per IP+email per 15 minutes" (PRD F1). Fails CLOSED if Redis is down:
    # with no way to count attempts, unlimited guessing must not be allowed.
    await limiter.enforce(
        "login",
        identifier,
        limit=settings.rate_limit_login_max,
        fail_open=False,
        **window,  # type: ignore[arg-type]
    )

    email = normalize_email(email_raw)
    user: User | None = None
    if email is not None:
        try:
            user = (
                await db.execute(select(User).where(func.lower(User.email) == email))
            ).scalar_one_or_none()
        except (SQLAlchemyError, OSError, TimeoutError) as exc:
            logger.warning("login: postgres unavailable (%s)", type(exc).__name__)
            raise ApiError(503, "unavailable", "the service is temporarily unavailable") from None

    if user is None or not user.is_active:
        await passwords.verify_unknown(password)  # same cost as a real check (no timing leak)
        raise _bad_credentials()
    if not await passwords.verify(user.password_hash, password):
        raise _bad_credentials()

    await limiter.refund("login", identifier, **window)  # type: ignore[arg-type]

    # Rotation: a session cookie the browser already had is destroyed, never reused, so an id an
    # attacker planted (session fixation) is worthless after login.
    if previous_sid:
        await sessions.destroy(previous_sid, user.id)
    sid = await sessions.create(user.id, user_agent, now)
    return user, sid, secrets.token_urlsafe(32)


async def logout(sessions: SessionStore, sid: str, user_id: uuid.UUID) -> None:
    if not await sessions.destroy(sid, user_id):
        # Say so: pretending the session is gone while it still works would be worse than an error.
        raise ApiError(503, "unavailable", "the service is temporarily unavailable")


async def change_password(
    db: AsyncSession,
    passwords: PasswordService,
    sessions: SessionStore,
    user_id: uuid.UUID,
    current_sid: str,
    old_password: str,
    new_password: str,
) -> None:
    """Set a new password and end every OTHER session. API keys are NOT touched (they have their
    own revoke, an explicit decision in ARCHITECTURE section 2.4)."""
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    if not await passwords.verify(user.password_hash, old_password):
        # 422 on the field, not 401: a 401 means "your session is invalid" and makes the frontend
        # log the user out, which a mistyped old password must not do.
        raise ApiError.validation([("old_password", "incorrect")])

    user.password_hash = await passwords.hash(new_password)
    await db.commit()

    # After the commit: from now on the new password is the only one that works. A thief holding
    # an old session must lose it. If Redis cannot do that, say so (503) instead of reporting
    # success; the password IS already changed, so the user can simply retry.
    if not await sessions.revoke_others(user_id, current_sid):
        raise ApiError(503, "unavailable", "the service is temporarily unavailable")
