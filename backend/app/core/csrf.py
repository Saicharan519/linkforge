"""CSRF defence for cookie-authenticated requests (ARCHITECTURE §2.4, docs/SECURITY.md).

The attack: you are logged in to LinkForge; a page on evil.example makes your browser send
`POST /api/v1/links/<id>/delete` to us. The browser attaches your session cookie on its own, so
without a defence the request looks legitimate. Two independent checks stop it:

1. Double-submit token. At login we set a readable cookie `lf_csrf`. Our own frontend copies its
   value into an `X-CSRF-Token` header. A page on another site cannot read our cookie and cannot
   set our header, so it cannot make the two match.
2. Origin check. Browsers attach an `Origin` header to cross-site POSTs and page scripts cannot
   forge it. It must be one of ALLOWED_ORIGINS. If `Origin` is absent, the browser's
   `Sec-Fetch-Site` header must say the request came from our own site (or from the user typing
   the address, "none").

Requests authenticated by API key skip both: no browser attaches an X-API-Key header on its own,
and we send no CORS headers, so another site cannot set it either (decision D26).

SameSite=Lax cookies are a third layer (browsers do not send them on cross-site POSTs) but it is
not relied on alone.
"""

import secrets

from fastapi import Request

from app.config import Settings
from app.core.cookies import CSRF_COOKIE
from app.core.errors import ApiError

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "x-csrf-token"


def _csrf_failed(message: str) -> ApiError:
    return ApiError(403, "csrf_failed", message)


def check_origin(request: Request, settings: Settings, *, strict: bool) -> None:
    """Raise 403 unless the request provably comes from one of our own pages.

    `strict=True` is for requests that carry a session cookie: with no `Origin` and no usable
    `Sec-Fetch-Site` the request is refused (a script that wants to act on a session should use an
    API key). `strict=False` is for register/login, which carry no cookie yet (decision D25): a
    request with NEITHER header is not from a browser (curl, k6), and login-CSRF needs a browser,
    so it is let through; any browser always sends one of the two.
    """
    origin = request.headers.get("origin")
    if origin is not None:
        if origin.strip().rstrip("/").lower() not in settings.allowed_origin_set:
            raise _csrf_failed("origin not allowed")
        return

    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is None:
        if strict:
            raise _csrf_failed("origin could not be verified")
        return
    if fetch_site.strip().lower() not in {"same-origin", "none"}:
        raise _csrf_failed("cross-site request refused")


def check_csrf_token(request: Request) -> None:
    """The X-CSRF-Token header must equal the lf_csrf cookie (constant-time comparison)."""
    header = request.headers.get(CSRF_HEADER, "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not header or not cookie or not secrets.compare_digest(header.encode(), cookie.encode()):
        raise _csrf_failed("missing or invalid CSRF token")


def protect_cookie_request(request: Request, settings: Settings) -> None:
    """Full check for an unsafe (state-changing) request authenticated by session cookie."""
    if request.method in SAFE_METHODS:
        return
    check_origin(request, settings, strict=True)
    check_csrf_token(request)
