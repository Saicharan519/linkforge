"""API keys (ARCHITECTURE §2.4): create, list, revoke, and authenticate a request by key.

A key looks like `lf_<prefix8>_<secret>`:
- `prefix8`  8 random letters/digits. Stored in clear and unique: it is the handle we look the key
             up by (and what the UI shows so a user can tell their keys apart). Letters and digits
             only, so the `_` after it is an unambiguous separator.
- `secret`   43 url-safe characters from 32 random bytes (256 bits).
Only `sha256(full key)` is stored, never the key. SHA-256 is enough here (unlike for passwords)
because the secret is 256 random bits: there is nothing to guess, so a slow hash adds no safety.
The plaintext is returned once, at creation, and cannot be recovered afterwards.
"""

import hashlib
import logging
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import ApiKey, User

logger = logging.getLogger(__name__)

PREFIX_ALPHABET = string.ascii_letters + string.digits
PREFIX_LENGTH = 8
MAX_PREFIX_ATTEMPTS = 5

# Shape check before any database work: junk in the header never reaches a query.
KEY_PATTERN = re.compile(r"lf_([A-Za-z0-9]{8})_([A-Za-z0-9_-]{43})")

# last_used_at is written at most once a minute per key (a busy key must not cause a write per
# request).
LAST_USED_DEBOUNCE = timedelta(minutes=1)


def hash_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode()).hexdigest()


def _new_key() -> tuple[str, str]:
    """(full key, prefix)."""
    prefix = "".join(secrets.choice(PREFIX_ALPHABET) for _ in range(PREFIX_LENGTH))
    return f"lf_{prefix}_{secrets.token_urlsafe(32)}", prefix


async def create_key(db: AsyncSession, user_id: uuid.UUID, name: str) -> tuple[ApiKey, str]:
    """Store a new key and return (row, plaintext). The plaintext is not kept anywhere."""
    for _ in range(MAX_PREFIX_ATTEMPTS):
        full_key, prefix = _new_key()
        row = ApiKey(user_id=user_id, name=name, prefix=prefix, key_hash=hash_key(full_key))
        try:
            # A savepoint: a prefix collision only undoes this INSERT.
            async with db.begin_nested():
                db.add(row)
                await db.flush()
        except IntegrityError:
            continue  # the unique prefix was taken (about 1 in 2e14): draw another
        await db.commit()
        await db.refresh(row)
        return row, full_key
    raise ApiError(503, "unavailable", "could not create a key, try again")


async def list_keys(db: AsyncSession, user_id: uuid.UUID) -> list[ApiKey]:
    stmt = (
        select(ApiKey)
        .where(ApiKey.user_id == user_id)  # every query is scoped by the owner
        .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
    )
    return list((await db.execute(stmt)).scalars())


async def revoke_key(
    db: AsyncSession, user_id: uuid.UUID, key_id: uuid.UUID, now: datetime
) -> None:
    """Revoke (idempotent). Someone else's key, or none, is a 404: ids cannot be probed."""
    row = (
        await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id))
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(404, "not_found", "api key not found")
    if row.revoked_at is None:
        row.revoked_at = now
        await db.commit()  # takes effect on the very next request: keys are looked up in Postgres


async def authenticate(
    db: AsyncSession, raw_key: str, now: datetime
) -> tuple[uuid.UUID, str] | None:
    """(user id, email) for a valid, unrevoked key of an active user; otherwise None.

    Raises 503 if Postgres cannot answer: an outage must not look like "your key is invalid".
    """
    match = KEY_PATTERN.fullmatch(raw_key)
    if match is None:
        return None
    try:
        row = (
            await db.execute(
                select(
                    ApiKey.id,
                    ApiKey.key_hash,
                    ApiKey.revoked_at,
                    ApiKey.user_id,
                    User.email,
                    User.is_active,
                )
                .join(User, User.id == ApiKey.user_id)
                .where(ApiKey.prefix == match.group(1))
            )
        ).first()
        if row is None:
            return None
        # Constant-time comparison of the hashes (no early exit that leaks how much matched).
        if not secrets.compare_digest(row.key_hash, hash_key(raw_key)):
            return None
        if row.revoked_at is not None or not row.is_active:
            return None
        await _touch_last_used(db, row.id, now)
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        logger.warning("api key lookup failed: postgres unavailable (%s)", type(exc).__name__)
        raise ApiError(503, "unavailable", "the service is temporarily unavailable") from None
    return row.user_id, row.email


async def _touch_last_used(db: AsyncSession, key_id: uuid.UUID, now: datetime) -> None:
    """Set last_used_at, but only if it is empty or older than a minute: a request from a busy
    key matches no row and writes nothing. A failure here never fails the request."""
    try:
        await db.execute(
            update(ApiKey)
            .where(
                ApiKey.id == key_id,
                (ApiKey.last_used_at.is_(None)) | (ApiKey.last_used_at < now - LAST_USED_DEBOUNCE),
            )
            .values(last_used_at=now)
        )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        logger.warning("api key: last_used_at not updated")
