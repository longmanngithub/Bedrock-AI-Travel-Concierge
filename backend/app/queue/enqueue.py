"""Enqueue a crew run as a background job, idempotently.

Two layers of idempotency so a double-submit (client retry, refresh) never runs
the crew twice:
  1. `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING` — the DB makes the
     row unique.
  2. arq `_job_id` dedupe — makes the enqueue itself a no-op on retry.
The client generates the key ONCE per send and reuses it on network retry.
"""
from __future__ import annotations

import uuid

from arq.connections import ArqRedis
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..errors import AppError, ErrorCode
from ..models import Conversation, Job, JobStatus, Message
from ..schemas import TripRequest
from . import keys


async def enqueue_itinerary(
    db: Session,
    pool: ArqRedis,
    *,
    trip_request: TripRequest,
    previous_record_id: int | None,
    user_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
    idempotency_key: str,
    reply_text: str | None = None,
) -> tuple[uuid.UUID, bool]:
    """Return (job_id, was_created) and recover a missed queue publish on retry."""
    create_placeholder = conversation_id is not None and reply_text is not None
    if create_placeholder:
        owned_conversation = db.scalar(
            select(Conversation.id).where(
                Conversation.id == conversation_id, Conversation.user_id == user_id
            ).with_for_update()
        )
        if owned_conversation is None:
            raise AppError(ErrorCode.E_NOT_FOUND)

    job_id = uuid.uuid4()
    payload = {
        "trip_request": trip_request.model_dump(mode="json"),
        "previous_record_id": previous_record_id,
    }
    stmt = (
        pg_insert(Job)
        .values(
            id=job_id,
            kind="crew_itinerary",
            status=JobStatus.queued,
            idempotency_key=idempotency_key,
            user_id=user_id,
            conversation_id=conversation_id,
            payload=payload,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(Job.id)
    )
    inserted_id = db.execute(stmt).scalar_one_or_none()

    was_created = inserted_id is not None
    if was_created:
        job_id = inserted_id
    else:
        existing = db.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing is None or existing.user_id != user_id or existing.conversation_id != conversation_id:
            raise AppError(ErrorCode.E_VALIDATION, log_detail="idempotency key cannot be reused for this request")
        job_id = existing.id
        if existing.status != JobStatus.queued:
            db.commit()
            return job_id, False

    # Commit the transcript placeholder before the worker can read the job.
    # A replay repairs a missing placeholder without duplicating an existing one.
    if create_placeholder and db.scalar(select(Message.id).where(Message.job_id == job_id)) is None:
        last_seq = db.scalar(
            select(func.max(Message.seq)).where(Message.conversation_id == conversation_id)
        )
        db.add(Message(
            conversation_id=conversation_id, role="assistant", content=reply_text,
            kind="job", job_id=job_id, seq=(last_seq or 0) + 1,
        ))
    db.commit()

    # A database commit can succeed before Redis does. Re-publish a still-
    # queued job on retry using the original arq ID, which deduplicates a
    # publish that already reached Redis.
    await pool.enqueue_job("run_itinerary_job", str(job_id), _job_id=keys.arq_job_id(str(job_id)))
    return job_id, was_created
