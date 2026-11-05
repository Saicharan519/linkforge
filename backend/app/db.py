"""Async database engine and session factory (SQLAlchemy 2.0 + asyncpg)."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings

# Decision D39: a code constant, not an env var. A dead database must fail fast (readiness, 503s)
# instead of hanging a request for the OS default TCP timeout.
CONNECT_TIMEOUT_SECONDS = 3


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # Test a pooled connection before use, so a restarted Postgres does not cause one failed
        # request per stale connection.
        pool_pre_ping=True,
        connect_args={"timeout": CONNECT_TIMEOUT_SECONDS},
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay readable after commit (no implicit lazy reload in async).
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed."""
    async with request.app.state.sessionmaker() as session:
        yield session
