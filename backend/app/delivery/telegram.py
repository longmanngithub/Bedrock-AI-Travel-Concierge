"""Telegram delivery via the raw Bot API (httpx), no python-telegram-bot
dependency — same "call the REST API directly" pattern as delivery/email.py
and crew/tools.py's Places/Tavily/Travelpayouts integrations.

Linking works by long-polling `getUpdates` (see queue/telegram_poll.py's arq
cron) rather than a webhook: a webhook needs a publicly reachable HTTPS URL
registered with Telegram, which a local dev box doesn't have, and polling
every few seconds is simple, needs no public endpoint, and is a perfectly
reasonable choice at this bot's scale (one account-linking message per user,
ever, plus outbound document sends which don't need updates at all).
"""
from __future__ import annotations

import logging
from functools import lru_cache

import httpx

from ..config import get_settings
from ..errors import AppError, ErrorCode

logger = logging.getLogger("app.delivery.telegram")

_API_BASE = "https://api.telegram.org/bot{token}"


def is_configured() -> bool:
    return bool(get_settings().telegram_bot_token)


def _base_url() -> str:
    token = get_settings().telegram_bot_token
    if not token:
        raise AppError(ErrorCode.E_DELIVERY_UNAVAILABLE, log_detail="TELEGRAM_BOT_TOKEN unset")
    return _API_BASE.format(token=token)


@lru_cache
def get_bot_username() -> str | None:
    """Cached for the process lifetime — used to build the t.me deep link."""
    if not is_configured():
        return None
    try:
        resp = httpx.get(f"{_base_url()}/getMe", timeout=10.0)
        resp.raise_for_status()
        return resp.json()["result"]["username"]
    except Exception:  # noqa: BLE001 - a missing username just disables the deep link
        logger.warning("telegram getMe failed", exc_info=True)
        return None


def send_message(chat_id: int, text: str) -> None:
    try:
        resp = httpx.post(
            f"{_base_url()}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        logger.warning("telegram sendMessage failed for chat %s", chat_id, exc_info=True)


def send_document(*, chat_id: int, pdf_bytes: bytes, filename: str, caption: str) -> None:
    """Raises AppError(E_DELIVERY_UNAVAILABLE | E_DELIVERY_FAILED) on failure."""
    if not is_configured():
        raise AppError(ErrorCode.E_DELIVERY_UNAVAILABLE, log_detail="TELEGRAM_BOT_TOKEN unset")
    try:
        resp = httpx.post(
            f"{_base_url()}/sendDocument",
            data={"chat_id": str(chat_id), "caption": caption},
            files={"document": (filename, pdf_bytes, "application/pdf")},
            timeout=30.0,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            raise AppError(ErrorCode.E_DELIVERY_FAILED, log_detail=f"telegram sendDocument: {body}")
    except httpx.HTTPStatusError as exc:
        raise AppError(
            ErrorCode.E_DELIVERY_FAILED,
            log_detail=f"telegram {exc.response.status_code}: {exc.response.text[:300]}",
        ) from exc
    except httpx.HTTPError as exc:
        raise AppError(ErrorCode.E_DELIVERY_FAILED, log_detail=f"telegram request failed: {exc}") from exc


def get_updates(offset: int | None, timeout: int = 0) -> list[dict]:
    """Long-poll wrapper for the linking cron. `timeout=0` = return immediately
    (used here since the cron itself provides the polling cadence — a
    blocking long-poll would tie up the worker's async loop)."""
    params: dict = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    try:
        resp = httpx.get(f"{_base_url()}/getUpdates", params=params, timeout=15.0)
        resp.raise_for_status()
        body = resp.json()
        return body.get("result", []) if body.get("ok") else []
    except httpx.HTTPError:
        logger.warning("telegram getUpdates failed", exc_info=True)
        return []
