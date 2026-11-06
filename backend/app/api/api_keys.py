"""/api/v1/api-keys: manage keys. SESSION ONLY: a key cannot create or revoke keys (so a leaked
key cannot mint more of itself), and these routes get the CSRF/Origin check from the session."""

import uuid

from fastapi import APIRouter, Response

from app.api.deps import DbSession, Now, SessionAuth
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.services import api_keys as service

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])


def _out(row) -> ApiKeyOut:  # type: ignore[no-untyped-def]
    return ApiKeyOut(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


@router.post("", status_code=201)
async def create_api_key(body: ApiKeyCreate, ctx: SessionAuth, db: DbSession) -> ApiKeyCreated:
    row, plaintext = await service.create_key(db, ctx.user_id, body.name)
    # The one and only time the plaintext leaves the server.
    return ApiKeyCreated(**_out(row).model_dump(), key=plaintext)


@router.get("")
async def list_api_keys(ctx: SessionAuth, db: DbSession) -> list[ApiKeyOut]:
    return [_out(row) for row in await service.list_keys(db, ctx.user_id)]


@router.delete("/{id}", status_code=204)
async def revoke_api_key(id: uuid.UUID, ctx: SessionAuth, db: DbSession, now: Now) -> Response:
    await service.revoke_key(db, ctx.user_id, id, now)
    return Response(status_code=204)
