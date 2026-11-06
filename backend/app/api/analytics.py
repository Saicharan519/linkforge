"""/api/v1 analytics: per-link and overview. Session or API key; read-only (so no CSRF check)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AuthUser, DbSession, Now
from app.schemas.analytics import LinkAnalytics, Overview, RangeName
from app.services import analytics as service

router = APIRouter(prefix="/api/v1", tags=["analytics"])

# `range` is a Python builtin, hence the alias on a differently named parameter.
Range = Annotated[RangeName, Query(alias="range", description="last N UTC days incl. today")]


@router.get("/links/{id}/analytics")
async def link_analytics(
    id: uuid.UUID, db: DbSession, user: AuthUser, now: Now, range_name: Range = "30d"
) -> LinkAnalytics:
    return await service.link_analytics(db, user.id, id, range_name, now)


@router.get("/analytics/overview")
async def overview(db: DbSession, user: AuthUser, now: Now, range_name: Range = "30d") -> Overview:
    return await service.overview(db, user.id, range_name, now)
