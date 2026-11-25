"""Job routes: SSE progress (resumable), poll, cancel, and the reconnect list.

The SSE endpoint is what makes "close the tab, come back, it's still there"
work: it replays from the browser's automatic `Last-Event-ID` (each frame is
emitted with an SSE `id:` = the Redis stream id) then live-tails. No client
bookkeeping. `/jobs?status=running` on page load re-attaches to any live job.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.cookies import verify_csrf
from ..auth.deps import current_user
from ..database import get_db
from ..errors import AppError, ErrorCode, USER_MESSAGES
from ..models import Job, JobStatus, Message, TripRecord, User
from ..queue import keys
from ..queue.settings import async_redis

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)

_TERMINAL = {JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled, JobStatus.expired}
_TERMINAL_EVENTS = {"done", "error", "cancelled"}


class JobView(BaseModel):
    id: uuid.UUID
    status: str
    kind: str
    percent: int
    record_id: int | None
    conversation_id: uuid.UUID | None
    error_code: str | None
    message: str | None
    created_at: str | None


def _job_view(job: Job) -> JobView:
    percent = 0
    if isinstance(job.progress, dict):
        percent = int(job.progress.get("percent", 0) or 0)
    message = None
    if job.error_code:
        try:
            message = USER_MESSAGES.get(ErrorCode(job.error_code))
        except ValueError:
            message = USER_MESSAGES[ErrorCode.E_INTERNAL]
    return JobView(
        id=job.id, status=job.status.value, kind=job.kind, percent=percent,
        record_id=job.result_record_id, conversation_id=job.conversation_id,
        error_code=job.error_code, message=message,
        created_at=job.created_at.isoformat() if job.created_at else None,
    )


def _owned_job(job_id: uuid.UUID, user: User, db: Session) -> Job:
    job = db.get(Job, job_id)
    # 404-shaped for a foreign/absent job so ids aren't enumerable.
    if job is None or job.user_id != user.id:
        raise AppError(ErrorCode.E_JOB_NOT_FOUND, log_detail="job not found/owned")
    return job


@router.get("", response_model=list[JobView])
def list_jobs(
    status: str | None = Query(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[JobView]:
    stmt = select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()).limit(50)
    if status:
        try:
            stmt = select(Job).where(Job.user_id == user.id, Job.status == JobStatus(status)).order_by(
                Job.created_at.desc()
            ).limit(50)
        except ValueError:
            raise AppError(ErrorCode.E_VALIDATION, log_detail="bad status")
    return [_job_view(j) for j in db.scalars(stmt).all()]


@router.get("/{job_id}", response_model=JobView)
def get_job(job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> JobView:
    return _job_view(_owned_job(job_id, user, db))


@router.post("/{job_id}/cancel", response_model=JobView)
async def cancel_job(
    job_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> JobView:
    verify_csrf(request)
    job = db.scalar(select(Job).where(Job.id == job_id, Job.user_id == user.id).with_for_update())
    if job is None:
        raise AppError(ErrorCode.E_JOB_NOT_FOUND, log_detail="job not found/owned")
    if job.status in _TERMINAL:
        return _job_view(job)

    placeholder = db.scalar(select(Message).where(Message.job_id == job.id))
    if placeholder is not None:
        placeholder.kind = "stopped"
        placeholder.content = "You stopped this response."
    job.status = JobStatus.cancelled
    job.finished_at = datetime.now(timezone.utc)
    view = _job_view(job)
    db.commit()

    try:
        # A queued job can be aborted before a worker has a chance to publish
        # anything. Persist its terminal SSE frame here so every subscriber
        # settles as cancelled. The worker also checks the durable job state.
        frame = json.dumps({"kind": "cancelled", "status": "cancelled"})
        async with async_redis() as r:
            await r.set(keys.cancel_key(str(job_id)), "1", ex=keys.CANCEL_TTL)
            await r.xadd(keys.events_key(str(job_id)), {"d": frame}, maxlen=keys.EVENTS_MAXLEN, approximate=True)
            await r.expire(keys.events_key(str(job_id)), keys.STATE_TTL)
            await r.set(keys.state_key(str(job_id)), frame, ex=keys.STATE_TTL)
    except RedisError:
        logger.warning("Could not publish cancellation for job %s", job_id)
    return view


@router.get("/{job_id}/events")
async def job_events(
    job_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> StreamingResponse:
    job = _owned_job(job_id, user, db)
    stream = keys.events_key(str(job_id))
    state_key = keys.state_key(str(job_id))
    terminal_now = _terminal_frame(db, job)
    last_event_id = request.headers.get("Last-Event-ID")

    async def gen():
        if terminal_now is not None:
            yield f"data: {json.dumps(terminal_now)}\n\n"
            return

        r = async_redis()
        try:
            # Capture the cursor before the snapshot so an update published
            # between them is still eligible for stream replay.
            if not last_event_id:
                tail = await r.xrevrange(stream, count=1)
                cursor = tail[0][0] if tail else "0"
                if isinstance(cursor, bytes):
                    cursor = cursor.decode()
                snap = await r.get(state_key)
                if snap:
                    payload = snap.decode() if isinstance(snap, bytes) else snap
                    yield f"data: {payload}\n\n"
                    if _is_terminal_event(payload):
                        return
            else:
                cursor = last_event_id

            idle = 0
            while True:
                if await request.is_disconnected():
                    return
                resp = await r.xread({stream: cursor}, block=5000, count=20)
                if not resp:
                    idle += 1
                    yield ": keep-alive\n\n"
                    # Safety: if the DB says terminal but no stream frame arrived
                    # (e.g. stream expired), settle from the DB.
                    if idle >= 3:
                        terminal = await run_in_threadpool(_fresh_terminal_frame, job_id)
                        if terminal is not None:
                            yield f"data: {json.dumps(terminal)}\n\n"
                            return
                        idle = 0
                    continue
                idle = 0
                for _stream, entries in resp:
                    for entry_id, fields in entries:
                        eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                        cursor = eid
                        raw = fields.get(b"d") or fields.get("d")
                        if raw is None:
                            continue
                        payload = raw.decode() if isinstance(raw, bytes) else raw
                        yield f"id: {eid}\ndata: {payload}\n\n"
                        if _is_terminal_event(payload):
                            return
        finally:
            await r.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


def _is_terminal_event(raw: str) -> bool:
    try:
        return json.loads(raw).get("kind") in _TERMINAL_EVENTS
    except (json.JSONDecodeError, AttributeError, TypeError):
        return False


def _terminal_frame(db: Session, job: Job | None) -> dict | None:
    status = job.status if job else JobStatus.expired
    if status not in _TERMINAL:
        return None
    if status == JobStatus.cancelled:
        return {"kind": "cancelled", "status": status.value}
    if status == JobStatus.succeeded and job and job.result_record_id is not None:
        record = db.get(TripRecord, job.result_record_id)
        if record is not None and record.user_id == job.user_id:
            return {
                "kind": "done", "status": status.value, "record_id": job.result_record_id,
                "itinerary": record.itinerary, "elapsed_seconds": round(record.elapsed_seconds, 1),
            }

    code = ErrorCode.E_JOB_EXPIRED if status == JobStatus.expired else ErrorCode.E_INTERNAL
    if job and job.error_code:
        try:
            code = ErrorCode(job.error_code)
        except ValueError:
            pass
    return {
        "kind": "error", "status": status.value, "code": code.value,
        "message": USER_MESSAGES[code], "retryable": AppError(code).retryable,
    }


def _fresh_terminal_frame(job_id: uuid.UUID) -> dict | None:
    from ..database import SessionLocal

    with SessionLocal() as db:
        return _terminal_frame(db, db.get(Job, job_id))
