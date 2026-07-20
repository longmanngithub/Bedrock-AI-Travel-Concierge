"""Auth + CSRF cookie handling.

Access + refresh cookies are httpOnly (JS can't read them). All three cookies
here use path="/" — the refresh cookie used to be scoped to "/auth" on the
theory that it only needs to reach the auth router's own endpoints, but that
path is matched against whatever URL the BROWSER actually requests, not
FastAPI's internal route path. Behind a reverse proxy that strips a prefix
(e.g. nginx stripping "/api/" per DEPLOYMENT.md §7.3 — the browser hits
"/api/auth/refresh", not "/auth/refresh"), a "/auth"-scoped cookie silently
never gets sent at all, which is a full outage of session refresh, not a
narrow-scoping nicety (confirmed live: every refresh 401'd with "no refresh
cookie" once the access token's 10-minute TTL ran out). The narrower scope
was never a meaningful security boundary anyway — the cookie is httpOnly, and
state-changing requests are already gated by the separate CSRF double-submit
token, not by cookie presence — so path="/" trades a cosmetic reduction in
cookie transmission for actually working under any reverse-proxy topology.
`SameSite=Lax` (not Strict) is required so the Google OAuth redirect carries
the session back. The CSRF cookie is deliberately NOT httpOnly — the SPA
reads it and echoes it in an `X-CSRF-Token` header (double-submit), checked
on state-changing routes.
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


def set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    settings = get_settings()
    secure = settings.cookies_secure
    response.set_cookie(
        ACCESS_COOKIE, access, httponly=True, secure=secure, samesite="lax",
        max_age=settings.access_token_ttl_seconds, path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE, refresh, httponly=True, secure=secure, samesite="lax",
        max_age=settings.refresh_token_ttl_seconds, path="/",
    )
    # Non-httpOnly CSRF token the SPA echoes back in a header.
    response.set_cookie(
        CSRF_COOKIE, secrets.token_urlsafe(24), httponly=False, secure=secure,
        samesite="lax", max_age=settings.refresh_token_ttl_seconds, path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/")


def verify_csrf(request: Request) -> None:
    """Double-submit check for state-changing requests. Raises E_CSRF on
    mismatch. GET/HEAD/OPTIONS are exempt (they don't mutate)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get("X-CSRF-Token")
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise AppError(ErrorCode.E_CSRF, log_detail="csrf token missing/mismatch")
