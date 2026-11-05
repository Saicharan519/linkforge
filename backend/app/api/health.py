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
    """Readiness: can this pod serve traffic? Postgres is required.

    (Redis health is added to this response in P2.1 when a Redis client exists; Redis down will
    mean "degraded but still ready", per docs/ARCHITECTURE.md §1.2 and DECISIONS D31/D38.)
    """
    try:
        # The extra second covers pool wait; the driver connect timeout is CONNECT_TIMEOUT_SECONDS.
        async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS + 1):
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception as exc:  # any failure means "not ready"; never let it become a 500
        # Log only the exception type: connection errors can carry host/user details.
        logger.warning("readyz: postgres check failed (%s)", type(exc).__name__)
        return JSONResponse(status_code=503, content={"status": "unavailable", "postgres": "down"})
    return JSONResponse(content={"status": "ok", "postgres": "ok"})
