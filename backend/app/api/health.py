"""Liveness and readiness probes.

kubelet uses these; they are not reachable via the public hosts.
"""

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db import CONNECT_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up and serving. Deliberately checks nothing else, so a database
    outage never makes Kubernetes restart healthy pods."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: can this pod serve traffic? Postgres is required; Redis is not.

    Redis down means "degraded but still ready" (docs/ARCHITECTURE.md §1.2, DECISIONS D31): every
    Redis use has a fallback, so taking the pod out of rotation would turn a partial outage into a
    full one. The Redis check goes through the same time-bounded helper as everything else.
    """
    redis_ok = await request.app.state.redis.call(
        "readyz", request.app.state.redis.client.ping(), False
    )
    redis_state = "ok" if redis_ok else "degraded"
    try:
        # The extra second covers pool wait; the driver connect timeout is CONNECT_TIMEOUT_SECONDS.
        async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS + 1):
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception as exc:  # any failure means "not ready"; never let it become a 500
        # Log only the exception type: connection errors can carry host/user details.
        logger.warning("readyz: postgres check failed (%s)", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "postgres": "down", "redis": redis_state},
        )
    return JSONResponse(content={"status": "ok", "postgres": "ok", "redis": redis_state})
