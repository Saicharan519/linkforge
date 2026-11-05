"""Small helpers that insert valid rows, so tests only spell out what they are about."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey, ClickEvent, Link, User


async def make_user(db: AsyncSession, email: str = "user@example.com") -> User:
    user = User(email=email, password_hash="not-a-real-hash")  # schema tests need no real hash
    db.add(user)
    await db.commit()
    return user


async def make_link(db: AsyncSession, user: User, code: str = "abc1234", **fields: object) -> Link:
    values = {"destination_url": "https://example.com/page", **fields}  # tests may override
    link = Link(user_id=user.id, code=code, **values)
    db.add(link)
    await db.commit()
    return link


def new_click(link: Link, event_id: uuid.UUID | None = None) -> ClickEvent:
    return ClickEvent(
        event_id=event_id or uuid.uuid4(),
        link_id=link.id,
        clicked_at=datetime.now(UTC),
        ip_hash="hmac-of-ip",
    )


def new_api_key(user: User, prefix: str = "ab12cd34") -> ApiKey:
    return ApiKey(user_id=user.id, name="ci", prefix=prefix, key_hash="sha256-of-key")
