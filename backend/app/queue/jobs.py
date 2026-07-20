"""The crew job body + the stale-job sweeper.

The heavy `run_crew` (torch, Chroma, multi-minute) is handed to a bounded
ThreadPoolExecutor so it never blocks the arq event loop. Progress flows through
a RedisProgressPublisher; the terminal frame is published from here. A raw
exception is classified into a safe ErrorCode — the traceback goes to
`jobs.error_detail` (never serialized), never to the client.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from ..config import get_settings
from ..database import SessionLocal
from ..delivery.memory import build_traveller_memory, update_preferences_after_trip
from ..errors import AppError, ErrorCode, USER_MESSAGES, classify_exception
from ..models import Conversation, Job, JobStatus, Message, TripRecord, User
from ..queue.delivery import deliver_itinerary_job
from ..queue.notifications import create_completion_delivery, send_response_email_job
from ..schemas import Itinerary, TripRequest
from . import keys
from .events import RedisProgressPublisher

logger = logging.getLogger("app.queue.jobs")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _reconcile_placeholder_message(db, job_id: str, *, kind: str, content: str | None, trip_record_id: int | None) -> None:
    """Update the Message row `_enqueue_plan` created at enqueue time (kind="job")
    in place, so a reloaded conversation shows the real outcome instead of a
    stuck "job" row. Best-effort: a missing row just means the turn was never
    linked to a conversation (e.g. the direct /itineraries endpoint), or the
    placeholder insert itself failed — neither should fail the job."""
    msg = db.scalar(select(Message).where(Message.job_id == job_id))
    if msg is None:
        return
    msg.kind = kind
    if content is not None:
        msg.content = content
    msg.trip_record_id = trip_record_id
    conv = db.get(Conversation, msg.conversation_id)
    if conv is not None:
        conv.updated_at = _utcnow()


def _load_previous(job: Job, previous_record_id: int | None) -> Itinerary | None:
    if not previous_record_id:
        return None
    with SessionLocal() as db:
        rec = db.get(TripRecord, previous_record_id)
        if rec is None:
            return None
        # Ownership: only revise a record that belongs to the same user.
        if job.user_id is not None and rec.user_id != job.user_id:
            return None
        try:
            return Itinerary(**rec.itinerary)
        except Exception:  # noqa: BLE001 - legacy/malformed row -> build fresh
            return None


def _generation_metadata() -> dict:
    settings = get_settings()
    return {
        "model": settings.model,
        "generation_config": {
            "rag_enabled": settings.rag_enabled,
            "crew_process": settings.crew_process,
            "crew_memory": settings.crew_memory,
            "quality_model": settings.quality_model,
        },
    }


async def run_itinerary_job(ctx: dict, job_id: str) -> None:
    from ..crew.crew import run_crew
    from ..crew.progress import JobCancelled

    redis_raw = ctx["redis_raw"]
    executor = ctx["executor"]
    publisher = RedisProgressPublisher(redis_raw, job_id, session_factory=SessionLocal)

    # Mark running.
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            logger.warning("job %s vanished before start", job_id)
            return
        if job.status in (JobStatus.cancelled, JobStatus.succeeded):
            return  # already terminal (cancel raced the start)
        job.status = JobStatus.running
        job.started_at = _utcnow()
        job.heartbeat_at = _utcnow()
        job.attempt = (job.attempt or 0) + 1
        payload = dict(job.payload or {})
        db.commit()

    publisher.emit_snapshot()

    try:
        trip_request = TripRequest(**payload["trip_request"])
    except Exception as exc:  # noqa: BLE001 - a bad payload is our bug, not the LLM's
        await _finish_error(job_id, publisher, AppError(ErrorCode.E_INTERNAL, log_detail=str(exc)))
        return

    memory_opt_in = False
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        previous = _load_previous(job, payload.get("previous_record_id"))
        traveller_memory = None
        if job is not None and job.user_id is not None:
            user = db.get(User, job.user_id)
            memory_opt_in = bool(user and user.memory_opt_in)
            if memory_opt_in:
                traveller_memory = build_traveller_memory(job.user_id, db)

    loop = asyncio.get_running_loop()

    def _blocking():
        # ThreadPoolExecutor does not propagate contextvars — run_crew sets
        # CURRENT_SINK/CURRENT_JOB itself from these args, inside this thread.
        return run_crew(
            trip_request,
            previous_itinerary=previous,
            progress=publisher,
            job_id=job_id,
            traveller_memory=traveller_memory,
        )

    try:
        itinerary, elapsed, tokens = await loop.run_in_executor(executor, _blocking)
    except (JobCancelled, asyncio.CancelledError):
        # arq cancellation raises CancelledError in the coroutine, while crew
        # checkpoints raise JobCancelled in the executor thread. Both must
        # settle the durable transcript and SSE stream the same way.
        await _finish_cancelled(job_id, publisher)
        return
    except AppError as exc:
        await _finish_error(job_id, publisher, exc)
        return
    except Exception as exc:  # noqa: BLE001
        await _finish_error(job_id, publisher, classify_exception(exc))
        return

    # A cancellation can race the final executor result. Never overwrite the
    # stopped transcript with a completed itinerary or trigger an email.
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None or job.status == JobStatus.cancelled:
            if job is not None:
                _reconcile_placeholder_message(
                    db, job_id, kind="stopped", content="You stopped this response.", trip_record_id=None
                )
                db.commit()
            publisher.emit_terminal({"kind": "cancelled", "status": "cancelled"})
            return

    # Persist the record + mark job succeeded.
    delivery_id: str | None = None
    telegram_user_id: str | None = None
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        record = TripRecord(
            system="crew",
            destination=trip_request.destination,
            request=trip_request.model_dump(mode="json"),
            itinerary=itinerary.model_dump(mode="json"),
            within_budget=itinerary.within_budget,
            elapsed_seconds=elapsed,
            user_id=job.user_id if job else None,
            conversation_id=job.conversation_id if job else None,
            job_id=job_id,
            **_generation_metadata(),
        )
        db.add(record)
        db.flush()
        record_id = record.id
        if job is not None:
            job.status = JobStatus.succeeded
            job.result_record_id = record_id
            job.finished_at = _utcnow()
        _reconcile_placeholder_message(db, job_id, kind="result", content=None, trip_record_id=record_id)
        message = db.scalar(select(Message).where(Message.job_id == job_id))
        if message is not None:
            message.extra = {
                "quick_replies": [
                    {"label": "Adjust this itinerary", "value": "I want to adjust this itinerary"},
                    {"label": "Add food recommendations", "value": "Add more food recommendations"},
                    {"label": "What should I pack?", "value": "What should I pack for this trip?"},
                ]
            }
            if job is not None and job.user_id is not None:
                user = db.get(User, job.user_id)
                if user is not None:
                    delivery = create_completion_delivery(db, message=message, user=user)
                    delivery_id = str(delivery.id) if delivery is not None else None
                    # Being linked at all is the opt-in here (unlike email,
                    # there's no separate toggle) — going through the /start
                    # flow is already a deliberate signal the traveller wants
                    # itineraries delivered there.
                    if user.telegram_chat_id is not None:
                        telegram_user_id = str(user.id)
        db.commit()
        # Only after a genuinely successful build — a trip that errored out
        # taught us nothing real about the traveller (see memory.py).
        if memory_opt_in and job is not None and job.user_id is not None:
            update_preferences_after_trip(job.user_id, trip_request, db)

    # Automatic delivery on every finished plan (fresh or replanned) — one
    # PDF per channel the traveller has actually opted into, run concurrently
    # so a slow email/Telegram send doesn't add to the other's latency before
    # the "done" frame below.
    deliveries = []
    if delivery_id:
        deliveries.append(send_response_email_job(ctx, delivery_id))
    if telegram_user_id:
        deliveries.append(deliver_itinerary_job(ctx, record_id, telegram_user_id, "telegram"))
    if deliveries:
        await asyncio.gather(*deliveries)

    publisher.emit_terminal({
        "kind": "done",
        "status": "succeeded",
        "record_id": record_id,
        "elapsed_seconds": round(elapsed, 1),
        "itinerary": itinerary.model_dump(mode="json"),
    })


async def _finish_error(job_id: str, publisher: RedisProgressPublisher, err: AppError) -> None:
    message = USER_MESSAGES.get(err.code, USER_MESSAGES[ErrorCode.E_INTERNAL])
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is not None and job.status == JobStatus.cancelled:
            # A cancel already settled this job durably (cancel_job /
            # _finish_cancelled). A non-JobCancelled exception racing in from
            # the in-flight crew run afterward must not stomp that terminal
            # state back to "failed" — re-affirm cancelled and stop.
            publisher.emit_terminal({"kind": "cancelled", "status": "cancelled"})
            return
        if job is not None:
            job.status = JobStatus.failed
            job.error_code = err.code.value
            job.error_detail = err.log_detail          # internal only
            job.finished_at = _utcnow()
        _reconcile_placeholder_message(db, job_id, kind="error", content=message, trip_record_id=None)
        db.commit()
    logger.error("job %s failed: %s | %s", job_id, err.code.value, err.log_detail)
    publisher.emit_terminal({
        "kind": "error",
        "status": "failed",
        "code": err.code.value,
        "message": message,
        "retryable": err.retryable,
    })


async def _finish_cancelled(job_id: str, publisher: RedisProgressPublisher) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is not None:
            job.status = JobStatus.cancelled
            job.finished_at = _utcnow()
        _reconcile_placeholder_message(
            db, job_id, kind="stopped", content="You stopped this response.", trip_record_id=None
        )
        db.commit()
    publisher.emit_terminal({"kind": "cancelled", "status": "cancelled"})


async def reap_stale_jobs(ctx: dict) -> None:
    """Cron: flip `running` jobs with a stale heartbeat to `expired` (worker
    OOM/crash), so a client SSE never hangs forever on a dead job."""
    from datetime import timedelta

    cutoff = _utcnow() - timedelta(seconds=keys.HEARTBEAT_STALE_SECONDS)
    with SessionLocal() as db:
        stale = db.scalars(
            select(Job).where(
                Job.status == JobStatus.running,
                Job.heartbeat_at.isnot(None),
                Job.heartbeat_at < cutoff,
            )
        ).all()
        for job in stale:
            job.status = JobStatus.expired
            job.error_code = ErrorCode.E_JOB_EXPIRED.value
            job.finished_at = _utcnow()
            _reconcile_placeholder_message(
                db, str(job.id), kind="error",
                content=USER_MESSAGES[ErrorCode.E_JOB_EXPIRED], trip_record_id=None,
            )
            logger.warning("reaped stale job %s", job.id)
        if stale:
            db.commit()
