"""Application factory. `uvicorn app.main:app` serves the module-level `app`."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import analytics, api_keys, auth, health, links, redirect
from app.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.host_guard import HostGuardMiddleware
from app.core.logging import configure_logging
from app.core.metrics_server import start_metrics_server
from app.core.observability import RequestMiddleware
from app.core.passwords import PasswordService
from app.core.rate_limit import RateLimiter
from app.core.redis import create_redis
from app.core.sessions import SessionStore
from app.core.stream_metrics import run_gauge_refresher
from app.db import create_engine, create_sessionmaker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # The engine is created lazily-connected: the app must boot even if Postgres is down, so that
    # /readyz can report the problem and Kubernetes keeps the pod out of rotation.
    engine = create_engine(app.state.settings)
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    # Same rule for Redis: the client connects lazily, so the app boots with Redis down (D56).
    app.state.redis = create_redis(app.state.settings)
    app.state.limiter = RateLimiter(app.state.redis)
    app.state.sessions = SessionStore(app.state.redis, app.state.settings.session_ttl_seconds)
    app.state.passwords = PasswordService()  # argon2, at most 2 hashes at once (D10)
    # Metrics: their own port (never the public one) and the click-stream gauges.
    metrics = start_metrics_server(app.state.settings.metrics_port)
    app.state.metrics_port = metrics.port if metrics else None
    refresher = None
    if metrics:
        refresher = asyncio.create_task(
            run_gauge_refresher(app.state.redis, app.state.settings.metrics_refresh_seconds)
        )
    try:
        yield
    finally:
        if refresher:
            refresher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresher
        if metrics:
            metrics.stop()
        await app.state.redis.aclose()
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)  # JSON lines on stdout

    app = FastAPI(
        title="LinkForge API",
        version="1.0.0",
        # Docs only when ENABLE_DOCS=true (dev). In production these URLs simply do not exist.
        docs_url="/api/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.enable_docs else None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    install_error_handlers(app)
    # Outermost: the short-link host is restricted before routing or authentication run.
    app.add_middleware(HostGuardMiddleware, public_host=settings.public_redirect_host)
    # Added last = OUTERMOST: request id, metrics and the access log see every request, including
    # the ones the host guard answers.
    app.add_middleware(RequestMiddleware)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(links.router)
    app.include_router(analytics.router)
    app.include_router(api_keys.router)
    # The redirect router `/{code}` must be included LAST: it matches any single-segment path and
    # would otherwise shadow every other root-level route (test_routes_not_shadowed_by_redirect).
    app.include_router(redirect.router)
    return app


app = create_app()
