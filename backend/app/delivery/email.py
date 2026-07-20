"""Email delivery via Resend's REST API.

Uses raw httpx against https://api.resend.com rather than the `resend`
SDK — one dependency-free call, consistent with how this codebase talks to
every other third-party API (Tavily, Google Places, Travelpayouts — see
crew/tools.py). `unset RESEND_API_KEY` is a normal, expected state (Phase 2's
"each integration is independently optional" pattern): callers check
`is_configured()` first and raise a clean E_DELIVERY_UNAVAILABLE rather than
attempting a call that can only fail.
"""
from __future__ import annotations

import base64
import logging
from html import escape

import httpx

from ..config import get_settings
from ..errors import AppError, ErrorCode
from ..schemas import Itinerary, TripRequest

logger = logging.getLogger("app.delivery.email")

_RESEND_URL = "https://api.resend.com/emails"


def is_configured() -> bool:
    return bool(get_settings().resend_api_key)


def _email_html(itinerary: Itinerary, trip: TripRequest) -> str:
    days = itinerary.num_days or trip.num_days
    # destination is free-text user input and summary is LLM/web-search
    # derived — both are untrusted and must be escaped before landing in an
    # HTML email sent from the app's verified sender domain.
    return (
        f"<p>Your {days}-day trip to <strong>{escape(itinerary.destination)}</strong> is ready — "
        "the full day-by-day itinerary is attached as a PDF.</p>"
        f"<p style=\"color:#667873;font-size:13px\">{escape(itinerary.summary or '')}</p>"
        "<p style=\"color:#8a9895;font-size:12px\">Sent by Bedrock, your AI travel concierge. "
        "This itinerary is AI-generated — please verify prices, hours, and availability "
        "directly with venues before booking.</p>"
    )


def send_itinerary_email(
    *,
    to_email: str,
    itinerary: Itinerary,
    trip: TripRequest,
    pdf_bytes: bytes,
    idempotency_key: str | None = None,
) -> str | None:
    """Raises AppError(E_DELIVERY_UNAVAILABLE | E_DELIVERY_FAILED) on any
    failure; never lets a Resend response/exception reach the caller raw.
    Returns Resend's message id, or None if the response body didn't have one.

    `idempotency_key`, when given, guards against a duplicate PDF send on a
    retried call (e.g. the automatic post-completion send) — same pattern as
    send_response_email's 24-hour idempotency guard below."""
    settings = get_settings()
    if not settings.resend_api_key:
        raise AppError(ErrorCode.E_DELIVERY_UNAVAILABLE, log_detail="RESEND_API_KEY unset")

    payload = {
        "from": settings.email_from,
        "to": [to_email],
        "subject": f"Your {itinerary.destination} itinerary",
        "html": _email_html(itinerary, trip),
        "attachments": [
            {
                "filename": f"{itinerary.destination.replace(' ', '_')}_itinerary.pdf",
                "content": base64.b64encode(pdf_bytes).decode("ascii"),
            }
        ],
    }
    headers = {"Authorization": f"Bearer {settings.resend_api_key}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        resp = httpx.post(_RESEND_URL, headers=headers, json=payload, timeout=20.0)
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        return data.get("id") if isinstance(data, dict) else None
    except httpx.HTTPStatusError as exc:
        # Resend's error body is a safe (non-secret) JSON reason — worth a
        # couple hundred chars in the log, never in the response to the client.
        raise AppError(
            ErrorCode.E_DELIVERY_FAILED,
            log_detail=f"resend {exc.response.status_code}: {exc.response.text[:300]}",
        ) from exc
    except httpx.HTTPError as exc:
        raise AppError(ErrorCode.E_DELIVERY_FAILED, log_detail=f"resend request failed: {exc}") from exc


def send_verification_email(*, to_email: str, code: str) -> None:
    """Deliver a short-lived registration code without persisting the plaintext."""
    settings = get_settings()
    if not settings.resend_api_key:
        raise AppError(ErrorCode.E_DELIVERY_UNAVAILABLE, log_detail="RESEND_API_KEY unset")
    payload = {
        "from": settings.email_from,
        "to": [to_email],
        "subject": "Your Bedrock verification code",
        "text": f"Your Bedrock verification code is {code}. It expires in 10 minutes.",
        "html": (
            "<p>Your Bedrock verification code is:</p>"
            f"<p style=\"font-size:28px;font-weight:700;letter-spacing:0.16em\">{code}</p>"
            "<p>This code expires in 10 minutes. If you did not request it, you can ignore this email.</p>"
        ),
    }
    try:
        resp = httpx.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json=payload,
            timeout=20.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise AppError(ErrorCode.E_DELIVERY_FAILED, log_detail=f"verification email failed: {exc}") from exc


def send_response_email(*, to_email: str, content: str, idempotency_key: str) -> str | None:
    """Send the full final response with Resend's 24-hour idempotency guard."""
    settings = get_settings()
    if not settings.resend_api_key:
        raise AppError(ErrorCode.E_DELIVERY_UNAVAILABLE, log_detail="RESEND_API_KEY unset")
    safe_html = "<br>".join(escape(line) for line in content.splitlines()) or "Your Bedrock response is ready."
    payload = {
        "from": settings.email_from,
        "to": [to_email],
        "subject": "Your Bedrock response is ready",
        "text": content,
        "html": (
            "<div style=\"font-family:Arial,sans-serif;line-height:1.55;color:#0e2226\">"
            f"<div>{safe_html}</div>"
            "<p style=\"color:#667873;font-size:12px\">Sent by Bedrock. AI-generated travel guidance should be verified before booking.</p>"
            "</div>"
        ),
    }
    try:
        resp = httpx.post(
            _RESEND_URL,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        return data.get("id") if isinstance(data, dict) else None
    except httpx.HTTPError as exc:
        raise AppError(ErrorCode.E_DELIVERY_FAILED, log_detail=f"completion email failed: {exc}") from exc
