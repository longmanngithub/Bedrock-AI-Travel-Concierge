"""Request-scoped correlation id.

Every request gets a uuid4 `request_id`, stashed in a ContextVar so any log
line (including deep inside the crew) can stamp it, and echoed back as the
`X-Request-Id` response header. This is what makes "never leak the exception"
survivable in practice: the user is shown `E_CREW_FAILED (req 8f2c…)`, and you
`grep` the logs for `8f2c` to find the full traceback.
"""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .config import get_settings

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

logger = logging.getLogger("app")


def get_request_id() -> str | None:
    """Return the current request's id, or None outside a request."""
    return _request_id.get()


def new_request_id() -> str:
    """Mint and install a fresh id (used by the worker, which has no request)."""
    rid = uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get("X-Request-Id")
        rid = (incoming or uuid.uuid4().hex)[:32]
        token = _request_id.set(rid)
        try:
            response = await call_next(request)
        finally:
            _request_id.reset(token)
        response.headers["X-Request-Id"] = rid
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defense-in-depth headers on every response.

    This API only ever serves JSON/SSE, never HTML, so a maximally
    restrictive CSP (`default-src 'none'`) is correct — nothing here needs to
    load a script/style/image/frame of its own. HSTS is gated on
    `cookies_secure` (the same flag that decides whether cookies are marked
    Secure) so it never fires on plain-http localhost, which would otherwise
    force the browser to upgrade every future request to HTTPS and break dev.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if get_settings().cookies_secure:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response
