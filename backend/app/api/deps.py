"""Shared FastAPI dependencies."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import ApiError
from app.db import get_db
from app.models import User


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


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
