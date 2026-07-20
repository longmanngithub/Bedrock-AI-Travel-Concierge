"""arq job: render the itinerary PDF and send it over the requested channel.

Deterministic and LLM-free (see delivery/__init__.py), so unlike
run_itinerary_job this never needs the bounded crew executor — it runs
directly on the worker's event loop via a threadpool offload for the
CPU-bound PDF render only.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ..database import SessionLocal
from ..delivery import email as email_delivery
from ..delivery import telegram as telegram_delivery
from ..delivery.pdf import render_itinerary_pdf
from ..errors import AppError, ErrorCode
from ..models import TripRecord, User
from ..schemas import Itinerary, TripRequest

logger = logging.getLogger("app.queue.delivery")


async def deliver_itinerary_job(ctx: dict, record_id: int, user_id: str, channel: str) -> None:
    with SessionLocal() as db:
        record = db.get(TripRecord, record_id)
        user = db.get(User, user_id)
        if record is None or user is None or record.user_id != user.id:
            logger.warning("deliver job: record %s not owned by user %s", record_id, user_id)
            return

        try:
            itinerary = Itinerary(**record.itinerary)
            trip = TripRequest(**record.request)
            loop = asyncio.get_running_loop()
            pdf_bytes = await loop.run_in_executor(None, render_itinerary_pdf, itinerary, trip)

            if channel == "email":
                email_delivery.send_itinerary_email(
                    to_email=user.email, itinerary=itinerary, trip=trip, pdf_bytes=pdf_bytes
                )
                detail = {"status": "sent", "to": user.email}
            elif channel == "telegram":
                if user.telegram_chat_id is None:
                    raise AppError(ErrorCode.E_DELIVERY_UNAVAILABLE, log_detail="no linked telegram chat")
                telegram_delivery.send_document(
                    chat_id=user.telegram_chat_id,
                    pdf_bytes=pdf_bytes,
                    filename=f"{itinerary.destination.replace(' ', '_')}_itinerary.pdf",
                    caption=f"Your {itinerary.destination} itinerary is ready!",
                )
                detail = {"status": "sent"}
            else:
                raise AppError(ErrorCode.E_VALIDATION, log_detail=f"unknown channel {channel!r}")
        except AppError as exc:
            detail = {"status": "failed", "code": exc.code.value}
            logger.warning("delivery failed record=%s channel=%s: %s", record_id, channel, exc.log_detail)
        except Exception as exc:  # noqa: BLE001 - a delivery bug must not crash the worker
            detail = {"status": "failed", "code": ErrorCode.E_INTERNAL.value}
            logger.exception("delivery job crashed record=%s channel=%s: %s", record_id, channel, exc)

        detail["sent_at"] = datetime.now(timezone.utc).isoformat()
        record.deliveries = {**(record.deliveries or {}), channel: detail}
        db.commit()
