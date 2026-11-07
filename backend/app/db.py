"""Async database engine and session factory (SQLAlchemy 2.0 + asyncpg)."""

import logging
import ssl
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import DatabaseSettings, Settings

logger = logging.getLogger(__name__)

# Decision D39: a code constant, not an env var. A dead database must fail fast (readiness, 503s)
# instead of hanging a request for the OS default TCP timeout.
CONNECT_TIMEOUT_SECONDS = 3


def database_ssl_context(settings: DatabaseSettings) -> ssl.SSLContext | None:
    """The TLS settings of a connection to Postgres (decisions D14, D85); None = plain TCP.

    Written out by hand instead of passing a string like "verify-full" to asyncpg, so that what
    each mode checks is visible here (and unit-tested), not hidden in a library default.
    """
    mode = settings.database_ssl_mode
    if mode == "disable":
        return None
    if mode == "require":
        # Encrypt, but trust whatever certificate the server shows (libpq's `require`).
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    # verify-ca / verify-full: trust ONLY the given CA file (no system CAs: a certificate for our
    # database must come from the CA we named, not from any public CA).
    context = ssl.create_default_context(cafile=settings.database_ssl_ca_file)
    context.verify_mode = ssl.CERT_REQUIRED
    # verify-full also checks that the certificate names the host we connected to. That is what
    # stops a valid-but-someone-else's certificate (any RDS instance has one signed by the same
    # CA) from being accepted for our database.
    context.check_hostname = mode == "verify-full"
    return context


def database_connect_args(settings: DatabaseSettings) -> dict[str, Any]:
    """Driver arguments shared by the API, the worker and Alembic."""
    args: dict[str, Any] = {"timeout": CONNECT_TIMEOUT_SECONDS}
    context = database_ssl_context(settings)
    # `disable` must say so: without an ssl argument asyncpg defaults to "prefer", i.e. it uses TLS
    # whenever the server offers it (found by tests/integration/test_db_tls.py).
    args["ssl"] = context if context is not None else False
    return args


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
        connect_args=database_connect_args(settings),
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
