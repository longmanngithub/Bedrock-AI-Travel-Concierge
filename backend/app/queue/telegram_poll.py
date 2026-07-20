"""arq cron: polls Telegram's getUpdates for `/start <code>` messages and
completes the account-linking flow those codes represent (see
routers/settings.py's link-code endpoint + delivery/telegram.py's docstring
on why polling instead of a webhook).

Runs only when TELEGRAM_BOT_TOKEN is set — a no-op tick otherwise, same
"unset = disabled" pattern as every other Phase 2/4 integration.
"""
from __future__ import annotations

import logging

from ..database import SessionLocal
from ..delivery.telegram import get_updates, is_configured, send_message
from ..queue import keys
from .settings import sync_redis

logger = logging.getLogger("app.queue.telegram_poll")


async def poll_telegram_updates(ctx: dict) -> None:
    if not is_configured():
        return

    r = sync_redis()
    try:
        raw_offset = r.get(keys.TELEGRAM_OFFSET_KEY)
        offset = int(raw_offset) + 1 if raw_offset else None
        updates = get_updates(offset)
        if not updates:
            return

        for update in updates:
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id is not None and text.startswith("/start"):
                parts = text.split(maxsplit=1)
                code = parts[1].strip() if len(parts) > 1 else ""
                if code:
                    # Own session+commit per update (not one commit for the
                    # whole batch): a DB failure on one /start (e.g. the
                    # chat_id unique constraint below) must not roll back or
                    # block every other update in the same poll tick.
                    with SessionLocal() as db:
                        _complete_link(db, r, code=code, chat_id=chat_id)
                else:
                    # A bare /start (the natural first thing anyone types
                    # to a Telegram bot) was previously ignored entirely —
                    # no reply at all, indistinguishable from the bot
                    # being broken. Always acknowledge it.
                    send_message(
                        chat_id,
                        "Hi! To connect this chat to your Bedrock account, "
                        "generate a link code from Settings > Telegram delivery "
                        "in the app, then send it here as /start <code>.",
                    )

        r.set(keys.TELEGRAM_OFFSET_KEY, updates[-1]["update_id"])
    except Exception:  # noqa: BLE001 - a poll tick must never crash the cron
        logger.warning("telegram poll tick failed", exc_info=True)
    finally:
        r.close()


def _complete_link(db, r, *, code: str, chat_id: int) -> None:
    from sqlalchemy.exc import IntegrityError

    from ..models import User

    raw_user_id = r.get(keys.telegram_link_key(code))
    if raw_user_id is None:
        send_message(chat_id, "That link code has expired. Generate a new one from Bedrock's settings.")
        return
    r.delete(keys.telegram_link_key(code))

    user_id = raw_user_id.decode() if isinstance(raw_user_id, bytes) else raw_user_id
    user = db.get(User, user_id)
    if user is None:
        return
    user.telegram_chat_id = chat_id
    try:
        db.commit()
    except IntegrityError:
        # telegram_chat_id is unique — this chat is already linked to a
        # DIFFERENT Bedrock account. The old code sent "You're connected!"
        # here regardless of whether the commit that followed actually
        # succeeded, so a real conflict silently failed to persist while the
        # user was told it worked (then got an unexplained "expired" on the
        # next poll tick's retry, since the one-time code was already
        # consumed from Redis above). Never claim success before a commit
        # has actually gone through.
        db.rollback()
        send_message(
            chat_id,
            "This Telegram account is already connected to a different Bedrock "
            "account. Disconnect it there first (Settings > Telegram delivery), "
            "or use a different Telegram account.",
        )
        return
    send_message(chat_id, "You're connected! Bedrock will send your itineraries here when you ask it to.")
    logger.info("telegram linked for user %s", user_id)
