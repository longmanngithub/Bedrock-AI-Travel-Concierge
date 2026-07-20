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
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import Job, JobStatus
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
) -> tuple[uuid.UUID, bool]:
    """Return (job_id, was_created). An existing key returns the prior job id
    with was_created=False and does not re-enqueue."""
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
    db.commit()

    if inserted_id is None:
        # Conflict: the row already exists — return the existing job id, no enqueue.
        existing = db.scalar(select(Job.id).where(Job.idempotency_key == idempotency_key))
        return existing, False

    await pool.enqueue_job("run_itinerary_job", str(job_id), _job_id=keys.arq_job_id(str(job_id)))
    return job_id, True
