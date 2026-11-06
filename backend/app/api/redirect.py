"""GET|HEAD /{code}: follow a short link.

This router MUST be included last in create_app(): `/{code}` matches any single-segment path, so
registered earlier it would shadow /healthz, /readyz and every other root-level route.
"""

from fastapi import APIRouter, Response
from fastapi.responses import RedirectResponse

from app.api.deps import AppSettings, DbSession, Now, RedisClient
from app.core.errors import ApiError
from app.core.urls import location_for
from app.services.redirect import resolve_destination

router = APIRouter(tags=["redirect"])


@router.get("/{code}", status_code=302)
async def follow(
    code: str, db: DbSession, redis: RedisClient, settings: AppSettings, now: Now
) -> RedirectResponse:
    resolved = await resolve_destination(db, redis, settings, code, now)
    # 302 (Found), NEVER 301: browsers cache a 301 permanently, so later visits would skip us
    # (lost analytics) and edits or deletions would never take effect for them.
    return RedirectResponse(location_for(resolved.destination_url), status_code=302)


# FastAPI does not add HEAD to a GET route automatically, so it is declared explicitly. HEAD gets
# the same status and Location but no body, and (later, P3.1) it never counts as a click.
@router.head("/{code}", status_code=302)
async def follow_head(
    code: str, db: DbSession, redis: RedisClient, settings: AppSettings, now: Now
) -> Response:
    try:
        resolved = await resolve_destination(db, redis, settings, code, now)
    except ApiError as exc:
        return Response(status_code=exc.status_code)  # status only: a HEAD response has no body
    return RedirectResponse(location_for(resolved.destination_url), status_code=302)
