"""FastAPI application exposing the Travel Concierge.

The crew no longer runs inside the request: a plannable turn ENQUEUES a
background job (arq) and returns a job id; the client subscribes to
`GET /jobs/{id}/events` (resumable SSE) for progress and the final itinerary.
This is what lets a run survive the browser closing. `main.py` deliberately does
NOT import the crew (that would pull torch into the API process) — only the
worker does.

Endpoints:
  GET  /health              - liveness probe
  POST /chat/stream         - streaming conversational front door (SSE)
  POST /chat                - non-streaming turn (enqueues on a plannable turn)
  POST /itineraries         - direct generation (baseline inline; crew enqueues)
  GET  /itineraries/{id}    - fetch a previously generated itinerary (owner-scoped)
  GET  /itineraries         - list the caller's recent generations
Auth, conversations, and jobs live in app/routers/.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import aclosing, asynccontextmanager
from enum import Enum

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth.cookies import ACCESS_COOKIE, verify_csrf
from .auth.deps import current_user, session_token_is_active
from .baseline import run_baseline
from .config import get_settings
from .conversation import (
    ChatRequest,
    ChatResponse,
    QuickReply,
    build_follow_ups,
    build_reply_messages,
    extract_turn,
    latest_previous_record_id,
    reply_without_model,
)
from .database import SessionLocal, check_schema_current, get_db
from .errors import AppError, ErrorCode, USER_MESSAGES, classify_exception, error_payload
from .llm import stream_call, strip_emoji
from .middleware import (
    RequestBodyLimitMiddleware, RequestIdMiddleware, SecurityHeadersMiddleware,
    get_request_id, suppress_provider_url_logging,
)
from .models import Conversation, Message, TripRecord, User
from .queue.enqueue import enqueue_itinerary
from .queue.keys import QUEUE_NAME
from .queue.settings import close_arq_pool, get_arq_pool
from .rate_limit import account_key, limiter
from .routing import model_for
from .routers.auth import router as auth_router
from .routers.conversations import router as conversations_router
from .routers.geo import router as geo_router
from .routers.jobs import router as jobs_router
from .routers.settings import router as settings_router
from .schemas import Itinerary, TripRequest

_settings = get_settings()
suppress_provider_url_logging()
limit_generation = limiter.shared_limit("20/hour", scope="generation", key_func=account_key)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _settings.production and (
        not _settings.cookies_secure
        or len(_settings.jwt_secret.encode()) < 32
        or _settings.jwt_secret.startswith(("change-me", "dev-"))
    ):
        raise RuntimeError("Production requires secure cookies and a strong JWT_SECRET")
    # Refuse to serve on a stale schema rather than failing mid-request.
    check_schema_current()
    # Warm the arq pool so the first enqueue isn't slow.
    try:
        await get_arq_pool()
    except Exception:  # noqa: BLE001 - queue may be briefly unavailable at boot
        pass
    yield
    await close_arq_pool()


app = FastAPI(title="AI Travel Concierge", version="2.0.0", lifespan=lifespan)

app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# Credentialed CORS (cookies): exact origins only — never "*". Same-origin
# behind nginx in production makes this largely moot, but it stays correct for
# local cross-origin dev (Next on :5173, API on :8000).
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "X-Expected-Account", "Last-Event-ID"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(geo_router)
app.include_router(jobs_router)
app.include_router(settings_router)


# ---------------------------------------------------------------------------
# Error handling — a raw exception string never reaches a client.
# ---------------------------------------------------------------------------
_logger = logging.getLogger("app")


@app.exception_handler(AppError)
async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    # `log_detail` is the whole point of AppError (see errors.py's docstring:
    # "the traceback goes to the logs, keyed by that same request id") — it was
    # previously captured on the exception and then silently dropped here.
    if exc.log_detail:
        _logger.warning(
            "app error [req=%s] code=%s: %s", get_request_id(), exc.code.value, exc.log_detail
        )
    return JSONResponse(
        status_code=exc.http_status,
        content=error_payload(exc, request_id=get_request_id()),
    )


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Return a SAFE field list (locations only), never the raw errors (which
    # echo submitted values). No exception string.
    fields = []
    for e in exc.errors()[:10]:
        loc = ".".join(str(p) for p in e.get("loc", []) if p not in ("body",))
        if loc:
            fields.append(loc)
    err = AppError(ErrorCode.E_VALIDATION, log_detail=str(exc.errors())[:500])
    payload = error_payload(err, request_id=get_request_id())
    payload["fields"] = fields
    return JSONResponse(status_code=err.http_status, content=payload)


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    _logger.exception("unhandled error [req=%s]", get_request_id())
    err = AppError(ErrorCode.E_INTERNAL, log_detail=str(exc))
    return JSONResponse(status_code=500, content=error_payload(err, request_id=get_request_id()))


class SystemChoice(str, Enum):
    crew = "crew"
    baseline = "baseline"


class ItineraryResponse(Itinerary):
    id: int
    system: str
    elapsed_seconds: float


def _generation_metadata(system: str) -> dict:
    settings = get_settings()
    if system == "baseline":
        return {"model": settings.model, "generation_config": {}}
    return {
        "model": settings.model,
        "generation_config": {
            "rag_enabled": settings.rag_enabled,
            "crew_process": settings.crew_process,
            "crew_memory": settings.crew_memory,
            "quality_model": settings.quality_model,
        },
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _build_trip_request(slots) -> TripRequest:
    return TripRequest(
        destination=slots.destination,
        origin=slots.origin,
        budget=slots.budget,
        currency=slots.currency or "USD",
        start_date=slots.start_date,
        end_date=slots.end_date,
        interests=slots.interests or [],
        travelers=slots.travelers or 1,
        pace=slots.pace or "balanced",
        special_requests=slots.special_requests or "",
        discussed_topics=slots.discussed_topics or "",
    )


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        return None


# Every quick-reply decision lives in conversation.py, next to the extraction
# it depends on (see build_follow_ups' docstring); this is only the local alias
# the route handlers below call.
_follow_ups = build_follow_ups


async def _persist_completed_reply(
    *, req: ChatRequest, user: User, content: str, kind: str, quick_replies: list[dict[str, str]]
) -> str | None:
    """Persist a direct completed reply (refusal/clarify/answer/smalltalk).

    Deliberately does NOT trigger a completion email — that's reserved for an
    actual finished plan (see queue/jobs.py's itinerary success path), not
    every conversational turn. A user who opts into "email completed
    responses" wants their itinerary, not an email for every clarifying
    question the assistant asks back.
    """
    conversation_id = _parse_uuid(req.conversation_id)
    if conversation_id is None:
        return None
    db = SessionLocal()
    try:
        conversation = db.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != user.id:
            return None
        seq = (db.scalar(select(func.max(Message.seq)).where(Message.conversation_id == conversation_id)) or 0) + 1
        message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            kind=kind,
            extra={"quick_replies": quick_replies},
            seq=seq,
        )
        db.add(message)
        db.commit()
        return str(message.id)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        _logger.warning("could not persist completed chat response (%s)", type(exc).__name__)
        return None
    finally:
        db.close()


async def _enqueue_plan(
    req: ChatRequest, extraction, user: User, reply_text: str
) -> dict:
    """Build the TripRequest and enqueue a crew job. Returns an SSE-ready frame
    (a `job` frame on success, or a `done`/`error` frame to settle the turn).
    The caller is always authenticated — both /chat and /chat/stream require
    `current_user` — so there is no anonymous branch here.

    On a fresh enqueue into a real conversation, also persists a placeholder
    Message (kind="job", job_id set) so the turn survives a closed tab: on
    reload, the client finds this trailing row and reconnects to the job's
    SSE stream instead of losing track of it. The worker (queue/jobs.py)
    updates this same row in place to kind="result"/"error" when the job
    settles — there is never a second row for the same turn."""
    request_id = get_request_id()

    try:
        trip_request = _build_trip_request(extraction.slots)
    except ValidationError:
        return {
            "kind": "done", "type": "clarify",
            "message": (
                "I need to double check a couple of details before I can plan "
                "this trip. Could you clarify?"
            ),
            "quick_replies": [],
        }

    previous_record_id = latest_previous_record_id(req.messages)
    idempotency_key = req.idempotency_key or uuid.uuid4().hex
    conversation_id = _parse_uuid(req.conversation_id)

    try:
        pool = await get_arq_pool()
    except Exception as exc:  # noqa: BLE001
        err = classify_exception(exc)
        err = AppError(ErrorCode.E_QUEUE_UNAVAILABLE, log_detail=err.log_detail)
        return {"kind": "error", **error_payload(err, request_id=request_id)}

    # enqueue_itinerary needs the pool + a sync Session; run it directly (it is
    # async but its DB work is quick).
    db = SessionLocal()
    try:
        job_id, created = await enqueue_itinerary(
            db, pool,
            trip_request=trip_request,
            previous_record_id=previous_record_id,
            user_id=user.id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            reply_text=reply_text,
        )
    except AppError as exc:
        return {"kind": "error", **error_payload(exc, request_id=request_id)}
    except Exception as exc:  # noqa: BLE001
        err = AppError(ErrorCode.E_QUEUE_UNAVAILABLE, log_detail=str(exc))
        return {"kind": "error", **error_payload(err, request_id=request_id)}
    finally:
        db.close()

    return {"kind": "job", "job_id": str(job_id), "status": "queued", "reused": not created}


@app.post("/chat/stream")
@limit_generation
@limiter.limit(_settings.rate_limit_generate)
async def chat_stream(
    req: ChatRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Streaming conversational front door (SSE).

    Requires a signed-in user — `current_user` raises 401 E_AUTH_REQUIRED
    before this body runs at all, so an anonymous caller never reaches the LLM.

    Frames: meta -> token(s) -> done | job | error. A plannable turn ends in a
    `job` frame; the client then subscribes to /jobs/{id}/events.
    """
    verify_csrf(request)
    messages = req.messages
    location_hint = req.location_hint if user.location_share_opt_in else None
    access_cookie = request.cookies.get(ACCESS_COOKIE)
    db.close()
    request_id = get_request_id()

    async def event_stream():
        try:
            extraction = await run_in_threadpool(extract_turn, messages, location_hint)
        except AppError as exc:
            _logger.info("chat extraction [req=%s] code=%s", request_id, exc.code.value)
            yield _sse({"kind": "error", **error_payload(exc, request_id=request_id)})
            return
        except Exception as exc:  # noqa: BLE001
            err = AppError(ErrorCode.E_EXTRACTION_FAILED, log_detail=str(exc))
            yield _sse({"kind": "error", **error_payload(err, request_id=request_id)})
            return

        route = getattr(extraction, "route", None) or (
            "plan" if extraction.ready_to_plan else ("refusal" if not extraction.on_topic else "clarify")
        )
        # meta.type drives which skeleton the client shows: only plan/revise get
        # the itinerary skeleton; everything else is a light typing indicator.
        meta_type = "result" if route in ("plan", "revise") else (
            "refusal" if route == "refusal" else "clarify"
        )
        yield _sse({"kind": "meta", "type": meta_type})

        reply_text = reply_without_model(
            messages,
            extraction,
            reuse_extracted=(
                _settings.groq_reuse_extracted_reply and model_for("extraction").startswith("groq/")
            ),
        )
        if reply_text is None:
            reply_task = "answer" if route == "answer" else "reply"
            iterator = stream_call(
                build_reply_messages(messages, extraction), temperature=0.7, task=reply_task
            )
            streamed: list[str] = []
            while True:
                token = await run_in_threadpool(next, iterator, None)
                if token is None:
                    break
                token = strip_emoji(token)
                streamed.append(token)
                if token:
                    yield _sse({"kind": "token", "text": token})
            reply_text = "".join(streamed).strip()
        else:
            yield _sse({"kind": "token", "text": reply_text})

        if not reply_text:
            reply_text = extraction.assistant_reply or "Let me help you plan that trip!"
            yield _sse({"kind": "token", "text": reply_text})

        if route == "refusal":
            quick_replies = _follow_ups(extraction)
            message_id = await _persist_completed_reply(
                req=req, user=user, content=reply_text, kind="refusal", quick_replies=quick_replies
            )
            yield _sse({
                "kind": "done", "type": "refusal", "message": reply_text,
                "quick_replies": quick_replies, "message_id": message_id,
            })
            return

        if route in ("clarify", "answer", "smalltalk"):
            quick_replies = _follow_ups(extraction)
            message_id = await _persist_completed_reply(
                req=req, user=user, content=reply_text, kind="clarify", quick_replies=quick_replies
            )
            yield _sse({
                "kind": "done", "type": "clarify", "message": reply_text,
                "quick_replies": quick_replies, "message_id": message_id,
            })
            return

        # plan / revise -> enqueue a background job.
        frame = await _enqueue_plan(req, extraction, user, reply_text)
        if frame.get("kind") == "job":
            # Prefix with the reply so the client shows the acknowledgement, then
            # attaches to the job stream.
            frame["message"] = reply_text
        yield _sse(frame)

    async def authorized_stream():
        next_check = 0.0
        async with aclosing(event_stream()) as events:
            async for frame in events:
                now = time.monotonic()
                if access_cookie and now >= next_check:
                    if not await run_in_threadpool(session_token_is_active, access_cookie):
                        yield _sse({"kind": "error", **error_payload(
                            AppError(ErrorCode.E_SESSION_REVOKED), request_id=request_id,
                        )})
                        return
                    next_check = now + 5
                yield frame

    return StreamingResponse(
        authorized_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/chat", response_model=ChatResponse)
@limit_generation
@limiter.limit(_settings.rate_limit_generate)
async def chat(
    req: ChatRequest,
    request: Request,
    user: User = Depends(current_user),
) -> ChatResponse:
    """Non-streaming turn. Requires a signed-in user (see chat_stream). A
    plannable turn enqueues a job and returns its id in `record_id`-adjacent
    fields via the message; kept mainly for parity/testing."""
    verify_csrf(request)
    try:
        extraction = await run_in_threadpool(extract_turn, req.messages, req.location_hint if user.location_share_opt_in else None)
    except Exception as exc:  # noqa: BLE001
        err = classify_exception(exc)
        return ChatResponse(type="error", message=USER_MESSAGES.get(err.code, USER_MESSAGES[ErrorCode.E_INTERNAL]))

    if not extraction.on_topic:
        reply_text = extraction.assistant_reply or "Let's keep this focused on travel."
        quick_replies = _follow_ups(extraction)
        await _persist_completed_reply(
            req=req, user=user, content=reply_text, kind="refusal", quick_replies=quick_replies
        )
        return ChatResponse(
            type="refusal", message=reply_text,
            quick_replies=[QuickReply(**reply) for reply in quick_replies],
        )
    if not extraction.ready_to_plan:
        reply_text = extraction.assistant_reply or "What travel detail can I help with next?"
        quick_replies = _follow_ups(extraction)
        await _persist_completed_reply(
            req=req, user=user, content=reply_text, kind="clarify", quick_replies=quick_replies
        )
        return ChatResponse(
            type="clarify", message=reply_text,
            quick_replies=[QuickReply(**reply) for reply in quick_replies],
        )

    frame = await _enqueue_plan(req, extraction, user, extraction.assistant_reply or "Building your itinerary…")
    if frame.get("kind") == "job":
        return ChatResponse(type="result", message="Building your itinerary…", record_id=None)
    return ChatResponse(type="error", message=frame.get("message", USER_MESSAGES[ErrorCode.E_INTERNAL]))


@app.post("/itineraries")
@limit_generation
@limiter.limit(_settings.rate_limit_generate)
async def create_itinerary(
    trip_request: TripRequest,
    request: Request,
    system: SystemChoice = Query(SystemChoice.crew),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Direct generation. Baseline runs inline (one LLM call, no torch). Crew is
    enqueued as a background job (the API process never imports the crew)."""
    verify_csrf(request)
    if system is SystemChoice.baseline:
        try:
            itinerary, elapsed, _tokens = await run_in_threadpool(run_baseline, trip_request)
        except Exception as exc:  # noqa: BLE001
            raise classify_exception(exc)
        record = TripRecord(
            system="baseline", destination=trip_request.destination,
            request=trip_request.model_dump(mode="json"),
            itinerary=itinerary.model_dump(mode="json"),
            within_budget=itinerary.within_budget, elapsed_seconds=elapsed,
            user_id=user.id, **_generation_metadata("baseline"),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return ItineraryResponse(id=record.id, system="baseline", elapsed_seconds=elapsed, **itinerary.model_dump())

    pool = await get_arq_pool()
    job_id, created = await enqueue_itinerary(
        db, pool, trip_request=trip_request, previous_record_id=None,
        user_id=user.id, conversation_id=None, idempotency_key=uuid.uuid4().hex,
    )
    return JSONResponse(status_code=202, content={"job_id": str(job_id), "reused": not created})


@app.get("/itineraries/{record_id}", response_model=ItineraryResponse)
def get_itinerary(
    record_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ItineraryResponse:
    record = db.get(TripRecord, record_id)
    # Owner-scoped: unowned (legacy/orphaned) records are nobody's to read.
    if record is None or record.user_id != user.id:
        raise AppError(ErrorCode.E_NOT_FOUND, log_detail="itinerary not found/owned")
    return ItineraryResponse(
        id=record.id, system=record.system, elapsed_seconds=record.elapsed_seconds, **record.itinerary
    )


class DeliverBody(BaseModel):
    channel: str = Field(pattern="^(email|telegram)$")


def _owned_record(record_id: int, user: User, db: Session) -> TripRecord:
    record = db.get(TripRecord, record_id)
    if record is None or record.user_id != user.id:
        raise AppError(ErrorCode.E_NOT_FOUND, log_detail="itinerary not found/owned")
    return record


@app.post("/itineraries/{record_id}/deliver")
@limiter.limit("6/hour", key_func=account_key)
async def deliver_itinerary(
    record_id: int, body: DeliverBody, request: Request,
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> JSONResponse:
    """Enqueues a background send (email or Telegram) of this itinerary's PDF.
    Deliberately not itself an arq `Job` row / SSE-tracked — delivery is
    deterministic and near-instant, so the client just polls
    GET /itineraries/{id}/delivery-status a couple of times rather than
    subscribing to a whole progress stream for one HTTP call."""
    verify_csrf(request)
    record = _owned_record(record_id, user, db)
    if body.channel == "telegram" and user.telegram_chat_id is None:
        raise AppError(ErrorCode.E_DELIVERY_UNAVAILABLE, log_detail="telegram not linked")

    pool = await get_arq_pool()
    if await pool.zcard(QUEUE_NAME) >= 100:
        raise AppError(ErrorCode.E_RATE_LIMITED, log_detail="delivery queue capacity reached")
    await pool.enqueue_job(
        "deliver_itinerary_job", record.id, str(user.id), body.channel, user.telegram_link_revision,
        _job_id=f"delivery:{record.id}:{body.channel}",
    )
    return JSONResponse(status_code=202, content={"status": "queued"})


@app.get("/itineraries/{record_id}/delivery-status")
def itinerary_delivery_status(
    record_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    record = _owned_record(record_id, user, db)
    return record.deliveries or {}


@app.get("/itineraries")
def list_itineraries(
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(
        select(TripRecord).where(TripRecord.user_id == user.id).order_by(TripRecord.id.desc()).limit(limit)
    ).all()
    return [
        {
            "id": r.id, "system": r.system, "destination": r.destination,
            "within_budget": r.within_budget, "elapsed_seconds": r.elapsed_seconds,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
