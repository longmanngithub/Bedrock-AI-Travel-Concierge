"""Auth + CSRF cookie handling.

Access + refresh cookies are httpOnly (JS can't read them). The refresh cookie
is scoped to `/auth` (not `/`) so it isn't sent on every request — only to the
auth router's own endpoints, which is also why the path is `/auth` rather than
the narrower `/auth/refresh`: per RFC 6265 path-matching, a `/auth/refresh`-
scoped cookie is never sent to a sibling path like `/auth/logout`, which would
silently break logout's server-side revocation. `SameSite=Lax` (not Strict) is
required so the Google OAuth redirect carries the session back. The CSRF
cookie is deliberately NOT httpOnly — the SPA reads it and echoes it in an
`X-CSRF-Token` header (double-submit), checked on state-changing routes.
"""
from __future__ import annotations

import secrets

from starlette.requests import Request
from starlette.responses import Response

from ..config import get_settings
from ..errors import AppError, ErrorCode

ACCESS_COOKIE = "ac_access"
REFRESH_COOKIE = "ac_refresh"
CSRF_COOKIE = "ac_csrf"
REFRESH_PATH = "/auth"


def set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    settings = get_settings()
    secure = settings.cookies_secure
    response.set_cookie(
        ACCESS_COOKIE, access, httponly=True, secure=secure, samesite="lax",
        max_age=settings.access_token_ttl_seconds, path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE, refresh, httponly=True, secure=secure, samesite="lax",
        max_age=settings.refresh_token_ttl_seconds, path=REFRESH_PATH,
    )
    # Non-httpOnly CSRF token the SPA echoes back in a header.
    response.set_cookie(
        CSRF_COOKIE, secrets.token_urlsafe(24), httponly=False, secure=secure,
        samesite="lax", max_age=settings.refresh_token_ttl_seconds, path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, REFRESH_PATH), (CSRF_COOKIE, "/")):
        response.delete_cookie(name, path=path)


def verify_csrf(request: Request) -> None:
    """Double-submit check for state-changing requests. Raises E_CSRF on
    mismatch. GET/HEAD/OPTIONS are exempt (they don't mutate)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get("X-CSRF-Token")
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise AppError(ErrorCode.E_CSRF, log_detail="csrf token missing/mismatch")
