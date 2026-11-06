"""Request id, HTTP metrics and the access log, in one middleware (ARCHITECTURE §7).

Installed OUTERMOST, so everything is measured: normal answers, host-guard 404s, rate-limit 429s.

- REQUEST ID: an incoming `X-Request-ID` is kept if it looks sane (so a caller or the ingress can
  correlate), otherwise a fresh one is made. It is put in a ContextVar (every log line of the
  request carries it) and returned in the response header.
- METRICS: `http_requests_total` and `http_request_duration_seconds`, labelled with the ROUTE
  TEMPLATE (`/api/v1/links/{id}`, `/{code}`), never the raw path: a raw path would create one time
  series per link or per scanner probe and blow up Prometheus. A request no route matched is
  labelled `unmatched`, so junk traffic is ONE series.
- ACCESS LOG: one JSON line per request (no headers, cookies, query string or client address).
  Successful health probes are not logged (kubelet calls them every few seconds); failed ones are.

Duration is measured up to the LAST BYTE of the response, which is before background tasks (the
click enqueue) run: it is what the visitor experiences.
"""

import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var
from app.core.metrics import HTTP_DURATION, HTTP_REQUESTS

logger = logging.getLogger("app.access")

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_KNOWN_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
_PROBES = {"/healthz", "/readyz"}
_PATH_LOG_MAX = 200


def pick_request_id(incoming: str | None) -> str:
    """Keep a well-formed incoming id, otherwise generate one. Newlines and odd characters are
    refused: the id ends up in logs and in a response header."""
    if incoming and _VALID_REQUEST_ID.fullmatch(incoming):
        return incoming
    return uuid.uuid4().hex


class RequestMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = next(
            (v.decode("latin-1") for k, v in scope["headers"] if k == b"x-request-id"), None
        )
        request_id = pick_request_id(incoming)
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500  # what is recorded if the app raises before sending anything
        finished = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status, finished
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", request_id.encode())]
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                if not finished:
                    finished = True
                    self._record(scope, status, time.perf_counter() - started)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            if not finished:
                finished = True
                self._record(scope, 500, time.perf_counter() - started)
            raise
        finally:
            request_id_var.reset(token)

    @staticmethod
    def _record(scope: Scope, status: int, seconds: float) -> None:
        method = scope["method"] if scope["method"] in _KNOWN_METHODS else "OTHER"
        route = scope.get("route")
        template = getattr(route, "path", None) or "unmatched"  # the matched route's template
        HTTP_REQUESTS.labels(method=method, route=template, status=str(status)).inc()
        HTTP_DURATION.labels(method=method, route=template, status=str(status)).observe(seconds)

        path = scope["path"]
        if path in _PROBES and status < 400:
            return
        logger.info(
            "request",
            extra={
                "fields": {
                    "method": method,
                    "path": path[:_PATH_LOG_MAX],
                    "route": template,
                    "status": status,
                    "duration_ms": round(seconds * 1000, 2),
                }
            },
        )
