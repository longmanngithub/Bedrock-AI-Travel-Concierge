"""Centralised error taxonomy.

The whole point of this module: **a raw exception string must never reach a
client.** Today `main.py` interpolates `{exc}` into user-facing chat bubbles in
several places, and `POST /itineraries` returns `Generation failed: {exc}` —
which will happily leak a Vertex AI stack trace containing the GCP project id.

Everything funnels through here instead. A caller raises `AppError(code)` (or a
low-level exception is passed to `classify_exception`), and the only string the
client ever sees is `USER_MESSAGES[code]` plus a request id it can quote to
support. The real traceback goes to the logs, keyed by that same request id.
"""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    # --- auth ---
    E_AUTH_REQUIRED = "E_AUTH_REQUIRED"
    E_AUTH_INVALID = "E_AUTH_INVALID"        # bad credentials — SAME code for
                                             # "no such email" and "wrong password"
    E_AUTH_EXPIRED = "E_AUTH_EXPIRED"        # access token expired -> client refreshes
    E_SESSION_REVOKED = "E_SESSION_REVOKED"  # refresh reuse detected -> hard re-login
    E_EMAIL_TAKEN = "E_EMAIL_TAKEN"
    E_OAUTH_FAILED = "E_OAUTH_FAILED"
    E_OAUTH_ALREADY_LINKED = "E_OAUTH_ALREADY_LINKED"  # that Google account is another user's
    E_OAUTH_UNLINK_BLOCKED = "E_OAUTH_UNLINK_BLOCKED"  # would leave the account with no way in
    E_EMAIL_UNVERIFIED = "E_EMAIL_UNVERIFIED"
    E_VERIFICATION_INVALID = "E_VERIFICATION_INVALID"
    E_VERIFICATION_EXPIRED = "E_VERIFICATION_EXPIRED"
    E_VERIFICATION_LOCKED = "E_VERIFICATION_LOCKED"
    E_VERIFICATION_COOLDOWN = "E_VERIFICATION_COOLDOWN"
    E_PASSWORD_POLICY = "E_PASSWORD_POLICY"
    E_CSRF = "E_CSRF"

    # --- request ---
    E_VALIDATION = "E_VALIDATION"
    E_NOT_FOUND = "E_NOT_FOUND"
    E_FORBIDDEN = "E_FORBIDDEN"
    E_RATE_LIMITED = "E_RATE_LIMITED"

    # --- llm ---
    E_LLM_RATE_LIMITED = "E_LLM_RATE_LIMITED"
    E_LLM_TIMEOUT = "E_LLM_TIMEOUT"
    E_LLM_UNAVAILABLE = "E_LLM_UNAVAILABLE"
    E_EXTRACTION_FAILED = "E_EXTRACTION_FAILED"

    # --- jobs ---
    E_QUEUE_UNAVAILABLE = "E_QUEUE_UNAVAILABLE"
    E_JOB_NOT_FOUND = "E_JOB_NOT_FOUND"
    E_JOB_EXPIRED = "E_JOB_EXPIRED"
    E_JOB_CANCELLED = "E_JOB_CANCELLED"
    E_CREW_FAILED = "E_CREW_FAILED"
    E_CREW_TIMEOUT = "E_CREW_TIMEOUT"

    # --- delivery (Phase 4: email / Telegram) ---
    E_DELIVERY_UNAVAILABLE = "E_DELIVERY_UNAVAILABLE"  # channel not configured, or not linked
    E_DELIVERY_FAILED = "E_DELIVERY_FAILED"            # the send attempt itself failed

    # --- location sharing ---
    E_GEOCODE_UNAVAILABLE = "E_GEOCODE_UNAVAILABLE"    # no geocoding API key configured
    E_GEOCODE_FAILED = "E_GEOCODE_FAILED"              # the lookup itself failed/found nothing

    # --- catch-all ---
    E_INTERNAL = "E_INTERNAL"


# The ONLY strings a client is ever shown. Deliberately generic — no field
# names, no provider text, no ids. Everything actionable is in the logs.
USER_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.E_AUTH_REQUIRED: "Please sign in to continue.",
    ErrorCode.E_AUTH_INVALID: "That email or password is incorrect.",
    ErrorCode.E_AUTH_EXPIRED: "Your session expired. Please try again.",
    ErrorCode.E_SESSION_REVOKED: "Your session is no longer valid. Please sign in again.",
    ErrorCode.E_EMAIL_TAKEN: "An account with that email already exists.",
    ErrorCode.E_OAUTH_FAILED: "We couldn't complete sign-in with Google. Please try again.",
    ErrorCode.E_OAUTH_ALREADY_LINKED: "That Google account is already linked to a different account.",
    ErrorCode.E_OAUTH_UNLINK_BLOCKED: "Set a password first so you don't get locked out, then disconnect Google.",
    ErrorCode.E_EMAIL_UNVERIFIED: "Verify your email address before signing in.",
    ErrorCode.E_VERIFICATION_INVALID: "That verification code is not valid.",
    ErrorCode.E_VERIFICATION_EXPIRED: "That verification code has expired. Request a new one.",
    ErrorCode.E_VERIFICATION_LOCKED: "Too many incorrect codes. Request a new one.",
    ErrorCode.E_VERIFICATION_COOLDOWN: "Please wait a moment before requesting another code.",
    ErrorCode.E_PASSWORD_POLICY: "Use 12 or more characters with uppercase, lowercase, a number, and a symbol.",
    ErrorCode.E_CSRF: "Something went wrong. Please refresh the page and try again.",
    ErrorCode.E_VALIDATION: "Some details didn't look right. Please check and try again.",
    ErrorCode.E_NOT_FOUND: "We couldn't find what you were looking for.",
    ErrorCode.E_FORBIDDEN: "You don't have access to that.",
    ErrorCode.E_RATE_LIMITED: "You're going a bit fast. Please wait a moment and try again.",
    ErrorCode.E_LLM_RATE_LIMITED: "We're handling a lot of requests right now. Please try again shortly.",
    ErrorCode.E_LLM_TIMEOUT: "That took too long to process. Please try again.",
    ErrorCode.E_LLM_UNAVAILABLE: "The planning service is temporarily unavailable. Please try again shortly.",
    ErrorCode.E_EXTRACTION_FAILED: "Something went wrong understanding your message. Please try again.",
    ErrorCode.E_QUEUE_UNAVAILABLE: "The planning service is temporarily unavailable. Please try again shortly.",
    ErrorCode.E_JOB_NOT_FOUND: "We couldn't find that plan.",
    ErrorCode.E_JOB_EXPIRED: "That plan is no longer available. Please start a new one.",
    ErrorCode.E_JOB_CANCELLED: "This plan was cancelled.",
    ErrorCode.E_CREW_FAILED: "We couldn't finish planning this trip. Please try again.",
    ErrorCode.E_CREW_TIMEOUT: "Planning this trip took too long. Please try again.",
    ErrorCode.E_DELIVERY_UNAVAILABLE: "That delivery option isn't set up yet.",
    ErrorCode.E_DELIVERY_FAILED: "We couldn't send that. Please try again.",
    ErrorCode.E_GEOCODE_UNAVAILABLE: "Location-based pricing isn't set up yet.",
    ErrorCode.E_GEOCODE_FAILED: "We couldn't determine your location. Please try again.",
    ErrorCode.E_INTERNAL: "Something went wrong. Please try again.",
}

# Which codes represent a transient condition the client may safely retry.
# Drives whether the frontend shows a "Try again" button.
_RETRYABLE: set[ErrorCode] = {
    ErrorCode.E_AUTH_EXPIRED,
    ErrorCode.E_RATE_LIMITED,
    ErrorCode.E_LLM_RATE_LIMITED,
    ErrorCode.E_LLM_TIMEOUT,
    ErrorCode.E_LLM_UNAVAILABLE,
    ErrorCode.E_QUEUE_UNAVAILABLE,
    ErrorCode.E_CREW_FAILED,
    ErrorCode.E_CREW_TIMEOUT,
    ErrorCode.E_DELIVERY_FAILED,
    ErrorCode.E_GEOCODE_FAILED,
    ErrorCode.E_INTERNAL,
}

_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.E_AUTH_REQUIRED: 401,
    ErrorCode.E_AUTH_INVALID: 401,
    ErrorCode.E_AUTH_EXPIRED: 401,
    ErrorCode.E_SESSION_REVOKED: 401,
    ErrorCode.E_EMAIL_TAKEN: 409,
    ErrorCode.E_OAUTH_FAILED: 400,
    ErrorCode.E_OAUTH_ALREADY_LINKED: 409,
    ErrorCode.E_OAUTH_UNLINK_BLOCKED: 409,
    ErrorCode.E_EMAIL_UNVERIFIED: 403,
    ErrorCode.E_VERIFICATION_INVALID: 400,
    ErrorCode.E_VERIFICATION_EXPIRED: 400,
    ErrorCode.E_VERIFICATION_LOCKED: 429,
    ErrorCode.E_VERIFICATION_COOLDOWN: 429,
    ErrorCode.E_PASSWORD_POLICY: 422,
    ErrorCode.E_CSRF: 403,
    ErrorCode.E_VALIDATION: 422,
    ErrorCode.E_NOT_FOUND: 404,
    ErrorCode.E_FORBIDDEN: 403,
    ErrorCode.E_RATE_LIMITED: 429,
    ErrorCode.E_LLM_RATE_LIMITED: 503,
    ErrorCode.E_LLM_TIMEOUT: 504,
    ErrorCode.E_LLM_UNAVAILABLE: 503,
    ErrorCode.E_EXTRACTION_FAILED: 502,
    ErrorCode.E_QUEUE_UNAVAILABLE: 503,
    ErrorCode.E_JOB_NOT_FOUND: 404,
    ErrorCode.E_JOB_EXPIRED: 410,
    ErrorCode.E_JOB_CANCELLED: 409,
    ErrorCode.E_CREW_FAILED: 502,
    ErrorCode.E_CREW_TIMEOUT: 504,
    ErrorCode.E_DELIVERY_UNAVAILABLE: 409,
    ErrorCode.E_DELIVERY_FAILED: 502,
    ErrorCode.E_GEOCODE_UNAVAILABLE: 409,
    ErrorCode.E_GEOCODE_FAILED: 502,
    ErrorCode.E_INTERNAL: 500,
}


class AppError(Exception):
    """An error whose *code* is safe to expose and whose *detail* never is.

    `log_detail` is for the logs only — it is NEVER serialized into a response.
    """

    def __init__(
        self,
        code: ErrorCode,
        *,
        log_detail: str | None = None,
        http_status: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.code = code
        self.log_detail = log_detail
        self.http_status = http_status if http_status is not None else _HTTP_STATUS.get(code, 500)
        self.retryable = retryable if retryable is not None else (code in _RETRYABLE)
        super().__init__(f"{code.value}: {log_detail or ''}")


def classify_exception(exc: BaseException) -> AppError:
    """Map an arbitrary low-level exception onto a safe `AppError`.

    Centralises the "429 / RESOURCE_EXHAUSTED" sniffing that is otherwise
    duplicated in `llm.py`. Anything unrecognised becomes `E_INTERNAL`, so a
    novel provider exception can never fall through to a raw string.
    """
    if isinstance(exc, AppError):
        return exc
    msg = str(exc)
    lowered = msg.lower()
    if "429" in msg or "resource_exhausted" in lowered or "rate limit" in lowered:
        return AppError(ErrorCode.E_LLM_RATE_LIMITED, log_detail=msg)
    if "timeout" in lowered or "timed out" in lowered:
        return AppError(ErrorCode.E_LLM_TIMEOUT, log_detail=msg)
    if any(s in lowered for s in ("unauthenticated", "permission denied", "invalid_argument", "api key")):
        return AppError(ErrorCode.E_LLM_UNAVAILABLE, log_detail=msg)
    return AppError(ErrorCode.E_INTERNAL, log_detail=msg)


def error_payload(err: AppError, *, request_id: str | None = None) -> dict:
    """The wire shape for an error — HTTP body AND SSE `error` frame.

    Contains only the code, a safe message, the retryable flag, and the request
    id. Never `detail`, never `exc`, never a field path.
    """
    return {
        "code": err.code.value,
        "message": USER_MESSAGES.get(err.code, USER_MESSAGES[ErrorCode.E_INTERNAL]),
        "retryable": err.retryable,
        "request_id": request_id,
    }
