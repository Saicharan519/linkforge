"""The two auth cookies (ARCHITECTURE §6.1).

`lf_session`  the session id. HttpOnly, so page scripts cannot read it.
`lf_csrf`     a random token the frontend reads and echoes in the X-CSRF-Token header
              (double-submit CSRF defence, checked in P2.5). NOT HttpOnly on purpose.

Both: SameSite=Lax, Path=/, Secure when COOKIE_SECURE is on (production), and NO Domain
attribute, which makes them host-only: the browser sends them to `app.<domain>` only and never
to the public short-link host `go.<domain>`.
"""

from fastapi import Response

from app.config import Settings

SESSION_COOKIE = "lf_session"
CSRF_COOKIE = "lf_csrf"


def set_session_cookies(response: Response, settings: Settings, sid: str, csrf_token: str) -> None:
    common = {
        "max_age": settings.session_ttl_seconds,
        "path": "/",
        "secure": settings.cookie_secure,
        "samesite": "lax",
    }
    response.set_cookie(SESSION_COOKIE, sid, httponly=True, **common)  # type: ignore[arg-type]
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, **common)  # type: ignore[arg-type]


def clear_session_cookies(response: Response, settings: Settings) -> None:
    # A cookie is removed by sending it again, with the same scope, already expired.
    for name, http_only in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(
            name, path="/", secure=settings.cookie_secure, httponly=http_only, samesite="lax"
        )
