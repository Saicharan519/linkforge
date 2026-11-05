"""/api/v1/links — thin router: validate input, call the service, return the schema."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import AppSettings, AuthUser, DbSession, Now
from app.schemas.link import LinkCreate, LinkList, LinkOut, LinkPatch, LinkStatus
from app.services import links as service

router = APIRouter(prefix="/api/v1/links", tags=["links"])


@router.post("", status_code=201)
async def create_link(
    body: LinkCreate, db: DbSession, user: AuthUser, settings: AppSettings, now: Now
) -> LinkOut:
    return await service.create_link(db, user.id, body, settings, now)


@router.get("")
async def list_links(
    db: DbSession,
    user: AuthUser,
    settings: AppSettings,
    now: Now,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status: LinkStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LinkList:
    return await service.list_links(
        db, user.id, settings, now, q=q, status=status, page=page, page_size=page_size
    )


@router.get("/{id}")
async def get_link(
    id: uuid.UUID, db: DbSession, user: AuthUser, settings: AppSettings, now: Now
) -> LinkOut:
    return await service.get_link(db, user.id, id, settings, now)


@router.patch("/{id}")
async def update_link(
    id: uuid.UUID,
    body: LinkPatch,
    db: DbSession,
    user: AuthUser,
    settings: AppSettings,
    now: Now,
) -> LinkOut:
    return await service.update_link(db, user.id, id, body, settings, now)


@router.delete("/{id}", status_code=204)
async def delete_link(id: uuid.UUID, db: DbSession, user: AuthUser) -> Response:
    await service.delete_link(db, user.id, id)
    return Response(status_code=204)
