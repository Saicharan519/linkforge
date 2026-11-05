"""One error shape for the whole API: {"error": {"code", "message", "details"}} (decision D30).

Every failure the client can see goes through here, including FastAPI's own validation errors,
unknown routes and unexpected crashes, so the frontend only ever has to parse one format.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Raise this from services/routers to produce a clean error response."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers

    @classmethod
    def validation(cls, problems: list[tuple[str, str]]) -> "ApiError":
        """422 with one {field, message} entry per problem."""
        return cls(
            422,
            "validation_error",
            "the request contains invalid data",
            details=[{"field": field, "message": message} for field, message in problems],
        )


def error_body(
    code: str, message: str, details: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # loc looks like ("body", "alias") or ("query", "page"); the first part is where it was.
        # Only the message is returned, never the offending input (it could be a password).
        details = [
            {
                "field": ".".join(str(part) for part in err["loc"][1:]) or str(err["loc"][0]),
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", "the request contains invalid data", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Raised by the framework itself: unknown route (404), wrong method (405), ...
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "internal_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # A bug. Log it with the stack trace, but never leak internals to the client.
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "an unexpected error occurred"),
        )
