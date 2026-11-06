"""Host guard: what the PUBLIC short-link host may serve (ARCHITECTURE §1.2, CLAUDE.md invariants).

One API process answers two public hostnames: the app host (`app.DOMAIN`: the frontend and the
JSON API) and the short-link host (`go.DOMAIN`: only redirects). The Ingress routes both to the
same API, so the API itself must make sure that the short-link host exposes nothing else. When the
request's Host equals PUBLIC_REDIRECT_HOST, ONLY `GET`/`HEAD /{code}` is served; everything else,
including `/api/...`, `/docs`, `/healthz`, `/readyz` and `/metrics`, is a plain 404.

Why 404 and not 401/403/405: the answer must not reveal that an API exists on that host.
Why a middleware (and not a router dependency): it runs before routing and before authentication,
so nothing behind it can be reached or even probed, and a route added by mistake later is covered
without anyone remembering to protect it.

The host comes from the `Host` header only. `X-Forwarded-Host` is ignored: any client can send
it, so trusting it would let a caller choose which rule applies to them.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.errors import error_body
from app.core.shortcode import ALIAS_PATTERN, RESERVED

_ALLOWED_METHODS = frozenset({"GET", "HEAD"})


def normalize_host(raw: str) -> str:
    """Lowercase, without a port: `GO.Example.com:8080` -> `go.example.com`."""
    host = raw.strip().lower()
    if host.startswith("["):  # IPv6 literal: [::1]:8000
        return host[: host.find("]") + 1] if "]" in host else host
    return host.partition(":")[0]


def _request_host(scope: Scope) -> str:
    for name, value in scope["headers"]:
        if name == b"host":
            return normalize_host(value.decode("latin-1"))
    return ""


def _is_short_link_request(scope: Scope) -> bool:
    """True only for `GET`/`HEAD` on a single path segment that could be a short code."""
    if scope["method"] not in _ALLOWED_METHODS:
        return False
    path = scope["path"]
    if not path.startswith("/") or "/" in path[1:]:
        return False  # "/", "/a/b", "/abc/"
    code = path[1:]
    # RESERVED holds the names that are real routes (healthz, readyz, api, ...): a link can never
    # have such a code, so they are never redirects, and must not reach the route that serves them.
    return bool(ALIAS_PATTERN.fullmatch(code)) and code.lower() not in RESERVED


class HostGuardMiddleware:
    def __init__(self, app: ASGIApp, public_host: str) -> None:
        self.app = app
        self.public_host = normalize_host(public_host)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            self.public_host
            and scope["type"] in {"http", "websocket"}
            and _request_host(scope) == self.public_host
            and (scope["type"] == "websocket" or not _is_short_link_request(scope))
        ):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            # (A HEAD response carries no body: the server drops it.)
            response = JSONResponse(error_body("not_found", "not found"), status_code=404)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
