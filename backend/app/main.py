"""Application factory. `uvicorn app.main:app` serves the module-level `app`."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, links
from app.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.db import create_engine, create_sessionmaker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # The engine is created lazily-connected: the app must boot even if Postgres is down, so that
    # /readyz can report the problem and Kubernetes keeps the pod out of rotation.
    engine = create_engine(app.state.settings)
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    try:
        yield
    finally:
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

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
    app.include_router(health.router)
    app.include_router(links.router)
    # NOTE: the redirect router `GET /{code}` must be included LAST (added in P1.5), otherwise it
    # would shadow every other root-level route.
    return app


app = create_app()
