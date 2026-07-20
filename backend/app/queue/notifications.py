"""Durable automatic completed-response email delivery."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..delivery.email import send_itinerary_email, send_response_email
from ..delivery.pdf import render_itinerary_pdf
from ..models import Message, ResponseEmailDelivery, TripRecord, User
from ..schemas import Itinerary, TripRequest


def create_completion_delivery(db: Session, *, message: Message, user: User) -> ResponseEmailDelivery | None:
    if not user.email_verified or not user.completion_email_opt_in:
        return None
    delivery = ResponseEmailDelivery(message_id=message.id, user_id=user.id, status="queued")
    db.add(delivery)
    db.flush()
    return delivery


async def send_response_email_job(ctx: dict, delivery_id: str) -> None:
    """Worker entry point; failures are audited but never affect chat output.

    A completed itinerary message (`trip_record_id` set) gets the actual
    boarding-pass PDF — the useful artifact, and what the manual "Email me
    this PDF" button already sends — rather than a plain-text recap. Any
    other completed response (a direct answer/clarify/refusal) has no trip
    record to render and gets the plain-text email as before.
    """
    with SessionLocal() as db:
        delivery = db.get(ResponseEmailDelivery, uuid.UUID(delivery_id))
        if delivery is None or delivery.status == "sent":
            return
        message = db.get(Message, delivery.message_id)
        user = db.get(User, delivery.user_id)
        if message is None or user is None or not user.email_verified or not user.completion_email_opt_in:
            if delivery is not None:
                delivery.status = "skipped"
                db.commit()
            return
        delivery.attempts += 1
        db.commit()
        record = db.get(TripRecord, message.trip_record_id) if message.trip_record_id else None
        try:
            if record is not None:
                itinerary = Itinerary(**record.itinerary)
                trip = TripRequest(**record.request)
                pdf_bytes = await asyncio.to_thread(render_itinerary_pdf, itinerary, trip)
                provider_id = await asyncio.to_thread(
                    send_itinerary_email,
                    to_email=user.email,
                    itinerary=itinerary,
                    trip=trip,
                    pdf_bytes=pdf_bytes,
                    idempotency_key=f"completion-{message.id}",
                )
                # Mirrors deliver_itinerary_job's audit shape so the manual
                # "Email me this PDF" button's mount-time hydration sees this
                # already sent, rather than offering a redundant duplicate.
                record.deliveries = {
                    **(record.deliveries or {}),
                    "email": {
                        "status": "sent", "to": user.email,
                        "sent_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
            else:
                provider_id = await asyncio.to_thread(
                    send_response_email,
                    to_email=user.email,
                    content=message.content,
                    idempotency_key=f"completion-{message.id}",
                )
            delivery.status = "sent"
            delivery.provider_id = provider_id
            delivery.sent_at = datetime.now(timezone.utc)
            delivery.error_code = None
        except Exception:
            delivery.status = "failed"
            delivery.error_code = "E_DELIVERY_FAILED"
        db.commit()
