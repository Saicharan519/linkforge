"""Async database engine and session factory (SQLAlchemy 2.0 + asyncpg)."""

import logging
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings

logger = logging.getLogger(__name__)

# Decision D39: a code constant, not an env var. A dead database must fail fast (readiness, 503s)
# instead of hanging a request for the OS default TCP timeout.
CONNECT_TIMEOUT_SECONDS = 3


def create_engine(
    settings: Settings, *, pool_size: int | None = None, max_overflow: int | None = None
) -> AsyncEngine:
    """`pool_size`/`max_overflow` override the API defaults (the worker uses 2 + 1)."""
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size if pool_size is None else pool_size,
        max_overflow=settings.db_max_overflow if max_overflow is None else max_overflow,
        # Test a pooled connection before use, so a restarted Postgres does not cause one failed
        # request per stale connection.
        pool_pre_ping=True,
        connect_args={"timeout": CONNECT_TIMEOUT_SECONDS},
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay readable after commit (no implicit lazy reload in async).
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed.

    Closing a session rolls back, and a rollback needs a live connection. If Postgres goes away
    while the request is finishing (a restart, a failover), that rollback raises, and an error
    raised while a dependency is torn down REPLACES the response: the client got a 500 even though
    the handler had already produced an answer (found in P8b with `kubectl delete pod postgres-0
    --grace-period=0`). The answer is already decided by then, so a failed close is logged and the
    dead connection is discarded instead.
    """
    session = request.app.state.sessionmaker()
    try:
        yield session
    finally:
        try:
            await session.close()
        except Exception as exc:  # noqa: BLE001  (any driver error: the connection is gone either way)
            logger.warning(
                "db session close failed (%s); discarding the connection", type(exc).__name__
            )
            try:
                await session.invalidate()
            except Exception:  # noqa: BLE001  (already broken: nothing more to do)
                logger.debug("db session invalidate failed too", exc_info=True)
