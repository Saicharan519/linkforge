"""GET|HEAD /{code}: follow a short link.

This router MUST be included last in create_app(): `/{code}` matches any single-segment path, so
registered earlier it would shadow /healthz, /readyz and every other root-level route.
"""

from fastapi import APIRouter, Response
from fastapi.responses import RedirectResponse

from app.api.deps import AppSettings, ClientIp, DbSession, Limiter, Now, RedisClient
from app.core.errors import ApiError
from app.core.urls import location_for
from app.services.redirect import Resolved, resolve_destination

router = APIRouter(tags=["redirect"])


async def _resolve(
    code: str,
    db: DbSession,
    redis: RedisClient,
    limiter: Limiter,
    settings: AppSettings,
    ip: str,
    now: Now,
) -> Resolved:
    # Step 1 of ARCHITECTURE §2.1: the per-IP rate limit comes first, before anything else is
    # looked up, so junk and flood traffic is rejected as cheaply as possible. It fails OPEN: if
    # Redis is down the redirect is still served (a limiter outage must not become a redirect
    # outage). When it could not reach Redis we also skip the cache steps, which would only
    # spend another timeout on the same dead server.
    redis_ok = await limiter.enforce(
        "redirect",
        ip,
        limit=settings.rate_limit_redirect_per_min,
        window_seconds=60,
        now_epoch=now.timestamp(),
        fail_open=True,
    )
    return await resolve_destination(db, redis, settings, code, now, redis_ok=redis_ok)


@router.get("/{code}", status_code=302)
async def follow(
    code: str,
    db: DbSession,
    redis: RedisClient,
    limiter: Limiter,
    settings: AppSettings,
    ip: ClientIp,
    now: Now,
) -> RedirectResponse:
    resolved = await _resolve(code, db, redis, limiter, settings, ip, now)
    # 302 (Found), NEVER 301: browsers cache a 301 permanently, so later visits would skip us
    # (lost analytics) and edits or deletions would never take effect for them.
    return RedirectResponse(location_for(resolved.destination_url), status_code=302)


# FastAPI does not add HEAD to a GET route automatically, so it is declared explicitly. HEAD gets
# the same status and Location but no body, and (later, P3.1) it never counts as a click. It is
# rate limited exactly like GET: it costs the same lookups.
@router.head("/{code}", status_code=302)
async def follow_head(
    code: str,
    db: DbSession,
    redis: RedisClient,
    limiter: Limiter,
    settings: AppSettings,
    ip: ClientIp,
    now: Now,
) -> Response:
    try:
        resolved = await _resolve(code, db, redis, limiter, settings, ip, now)
    except ApiError as exc:
        # Status (and Retry-After on a 429) only: a HEAD response has no body.
        return Response(status_code=exc.status_code, headers=exc.headers)
    return RedirectResponse(location_for(resolved.destination_url), status_code=302)
