"""Job routes: SSE progress (resumable), poll, cancel, and the reconnect list.

The SSE endpoint is what makes "close the tab, come back, it's still there"
work: it replays from the browser's automatic `Last-Event-ID` (each frame is
emitted with an SSE `id:` = the Redis stream id) then live-tails. No client
bookkeeping. `/jobs?status=running` on page load re-attaches to any live job.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.cookies import verify_csrf
from ..auth.deps import current_user
from ..database import get_db
from ..errors import AppError, ErrorCode, USER_MESSAGES
from ..models import Job, JobStatus, Message, User
from ..queue import keys
from ..queue.settings import async_redis, get_arq_pool

router = APIRouter(prefix="/jobs", tags=["jobs"])

_TERMINAL = {JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled, JobStatus.expired}


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
    job = _owned_job(job_id, user, db)
    if job.status in _TERMINAL:
        return _job_view(job)

    r = async_redis()
    try:
        await r.set(keys.cancel_key(str(job_id)), "1", ex=keys.CANCEL_TTL)
        try:
            pool = await get_arq_pool()
            await pool.abort_job(keys.arq_job_id(str(job_id)))
        except Exception:  # noqa: BLE001 - abort is best-effort (job may be mid-run)
            pass

        # Settle the durable record immediately rather than waiting for a crew
        # checkpoint. This prevents stale-job reaping from overwriting the
        # user's stopped feedback if arq aborts a running coroutine first.
        placeholder = db.scalar(select(Message).where(Message.job_id == job.id))
        if placeholder is not None:
            placeholder.kind = "stopped"
            placeholder.content = "You stopped this response."
        job.status = JobStatus.cancelled
        job.finished_at = datetime.now(timezone.utc)
        db.commit()

        # A queued job can be aborted before a worker has a chance to publish
        # anything. Persist its terminal SSE frame here so every subscriber
        # settles as cancelled rather than eventually looking expired.
        frame = json.dumps({"kind": "cancelled", "status": "cancelled"})
        await r.xadd(keys.events_key(str(job_id)), {"d": frame}, maxlen=keys.EVENTS_MAXLEN, approximate=True)
        await r.expire(keys.events_key(str(job_id)), keys.STATE_TTL)
        await r.set(keys.state_key(str(job_id)), frame, ex=keys.STATE_TTL)
    finally:
        await r.aclose()
    return _job_view(job)


@router.get("/{job_id}/events")
async def job_events(
    job_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> StreamingResponse:
    job = _owned_job(job_id, user, db)
    stream = keys.events_key(str(job_id))
    state_key = keys.state_key(str(job_id))
    terminal_now = job.status in _TERMINAL
    last_event_id = request.headers.get("Last-Event-ID")

    async def gen():
        r = async_redis()
        try:
            # Terminal already: emit the stored final state and close.
            if terminal_now:
                snap = await r.get(state_key)
                if snap:
                    yield f"data: {snap.decode() if isinstance(snap, bytes) else snap}\n\n"
                else:
                    yield "data: " + json.dumps({
                        "kind": "error", "status": job.status.value,
                        "code": job.error_code or ErrorCode.E_JOB_EXPIRED.value,
                        "message": USER_MESSAGES[ErrorCode.E_JOB_EXPIRED],
                    }) + "\n\n"
                return

            # Fresh viewer (no Last-Event-ID): send the snapshot once, then tail
            # from the current stream tail so we don't replay every step frame.
            if not last_event_id:
                snap = await r.get(state_key)
                if snap:
                    yield f"data: {snap.decode() if isinstance(snap, bytes) else snap}\n\n"
                tail = await r.xrevrange(stream, count=1)
                cursor = tail[0][0] if tail else "0"
                if isinstance(cursor, bytes):
                    cursor = cursor.decode()
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
                        fresh = _fresh_status(job_id)
                        if fresh in _TERMINAL:
                            snap = await r.get(state_key)
                            if snap:
                                yield f"data: {snap.decode() if isinstance(snap, bytes) else snap}\n\n"
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
                        try:
                            kind = json.loads(payload).get("kind")
                        except Exception:  # noqa: BLE001
                            kind = None
                        if kind in ("done", "error", "cancelled"):
                            return
        finally:
            await r.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


def _fresh_status(job_id: uuid.UUID) -> JobStatus:
    from ..database import SessionLocal

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        return job.status if job else JobStatus.expired
