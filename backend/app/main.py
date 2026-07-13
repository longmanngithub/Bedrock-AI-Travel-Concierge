"""FastAPI application exposing the Travel Concierge.

Endpoints:
  GET  /health              - liveness probe
  POST /chat                - conversational front door for the Bedrock UI
  POST /itineraries         - generate an itinerary (crew or baseline)
  GET  /itineraries/{id}    - fetch a previously generated itinerary
  GET  /itineraries         - list recent generations
"""
from __future__ import annotations

import json
import re
import time
import asyncio
from enum import Enum

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.orm import Session

from .baseline import run_baseline
from .config import get_settings
from .conversation import (
    ChatRequest,
    ChatResponse,
    QuickReply,
    build_reply_messages,
    extract_turn,
    latest_previous_record_id,
)
from .crew.crew import run_crew
from .database import SessionLocal, get_db, init_db
from .llm import stream_call, strip_emoji
from .models import TripRecord
from .schemas import Itinerary, TripRequest

app = FastAPI(title="AI Travel Concierge", version="1.0.0")

# Scoped to the actual frontend origin(s) (config.py: ALLOWED_ORIGINS), not a
# wildcard — a wildcard would let any website's client-side JS call this API
# using a visiting browser's own network access (the browser, not the
# attacker's server, would be making the request, so IP allow-listing alone
# wouldn't stop it).
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.allowed_origins_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# Per-client-IP rate limiting on the LLM-backed endpoints (see config.py:
# rate_limit_generate) — without this, the crew's own `max_rpm` only protects
# the *LLM provider's* quota, nothing stops one client from issuing unlimited
# requests against *this server*.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def require_api_key(x_api_key: str | None = Header(None)) -> None:
    """Optional API-key gate (config.py: API_KEY).

    A no-op when API_KEY is unset, so the app keeps working with zero
    configuration for local development; set API_KEY before exposing the API
    beyond localhost, since none of these routes otherwise require identity.
    """
    settings = get_settings()
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")


class SystemChoice(str, Enum):
    crew = "crew"
    baseline = "baseline"


class ItineraryResponse(Itinerary):
    id: int
    system: str
    elapsed_seconds: float


def _fetch_previous_itinerary(
    db: Session, messages: list, client_id: str | None
) -> Itinerary | None:
    """Look up the exact prior itinerary for this trip, if this turn is a
    confirmed replan of one — see `latest_previous_record_id`. Returns None on
    a first build, or if the id can't be resolved (e.g. an old conversation
    saved before this field existed); either way the crew just builds fresh.

    SECURITY: the record id is embedded in a marker string the *frontend*
    writes into its own chat transcript, which the *client* then resends —
    nothing stops a client from forging that marker with an arbitrary id and
    reading (or, worse, revising) another client's itinerary. `client_id` is
    an opaque token the frontend generates once and keeps in localStorage
    (never a real credential, but unguessable in practice); a lookup only
    succeeds if it matches the id the record was actually created under, so a
    forged/guessed record_id from a different client resolves to "not found"
    and the crew just builds fresh instead of leaking someone else's trip.
    """
    record_id = latest_previous_record_id(messages)
    if record_id is None:
        return None
    record = db.get(TripRecord, record_id)
    if record is None:
        return None
    if record.client_id != client_id:
        return None
    try:
        return Itinerary(**record.itinerary)
    except Exception:  # noqa: BLE001 - malformed/legacy row, just build fresh
        return None


def _generation_metadata(system: str) -> dict:
    """Snapshot of the model/config that produced a generation, for audit and
    reproducibility — so a later `.env` model/config change doesn't make a
    past TripRecord unexplainable."""
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


@app.on_event("startup")
def _startup() -> None:
    # Create tables if they don't exist. If the DB is unreachable we let the
    # error surface clearly rather than starting in a broken state.
    init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
@limiter.limit(_settings.rate_limit_generate)
async def chat(
    req: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    _api_key: None = Depends(require_api_key),
) -> ChatResponse:
    """One turn of the Bedrock chat flow.

    Stateless: the client resends the full running transcript every turn. A
    single LLM call classifies intent and extracts trip slots; only once all
    required slots are present does this route touch the crew or the DB.
    """
    try:
        extraction = await run_in_threadpool(extract_turn, req.messages)
    except Exception as exc:  # noqa: BLE001 - LLM/transport failure, not a bad response
        return ChatResponse(
            type="error",
            message=f"Something went wrong understanding your message: {exc}",
        )

    if not extraction.on_topic:
        return ChatResponse(type="refusal", message=extraction.assistant_reply)

    if not extraction.ready_to_plan:
        return ChatResponse(
            type="clarify",
            message=extraction.assistant_reply,
            quick_replies=[
                QuickReply(label=o, value=o) for o in extraction.quick_reply_options
            ],
        )

    slots = extraction.slots
    try:
        trip_request = TripRequest(
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
    except ValidationError as exc:
        return ChatResponse(
            type="clarify",
            message=(
                "I need to double check a couple of details before I can plan "
                f"this trip: {exc}. Could you clarify?"
            ),
        )

    previous_itinerary = _fetch_previous_itinerary(db, req.messages, req.client_id)
    try:
        itinerary, elapsed, _tokens = await run_in_threadpool(
            run_crew, trip_request, False, previous_itinerary=previous_itinerary
        )
    except Exception as exc:  # noqa: BLE001 - surface a plain, honest failure
        return ChatResponse(
            type="error",
            message=(
                "I couldn't generate your itinerary — the planning system hit "
                f"an error ({exc}). Please try again in a moment."
            ),
        )

    record = TripRecord(
        system="crew",
        destination=trip_request.destination,
        request=trip_request.model_dump(mode="json"),
        itinerary=itinerary.model_dump(mode="json"),
        within_budget=itinerary.within_budget,
        elapsed_seconds=elapsed,
        client_id=req.client_id,
        **_generation_metadata("crew"),
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return ChatResponse(
        type="result",
        message="Here's your itinerary!",
        itinerary=itinerary,
        trip_request=trip_request,
        record_id=record.id,
        elapsed_seconds=elapsed,
    )


def _sse(payload: dict) -> str:
    """Encode one Server-Sent-Events frame from a JSON-serialisable payload."""
    return f"data: {json.dumps(payload)}\n\n"


def _build_trip_request(slots) -> TripRequest:
    """Assemble a validated TripRequest from extracted slots (may raise)."""
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


@app.post("/chat/stream")
@limiter.limit(_settings.rate_limit_generate)
async def chat_stream(
    req: ChatRequest, request: Request, _api_key: None = Depends(require_api_key)
) -> StreamingResponse:
    """Streaming version of /chat (Server-Sent Events).

    Emits, in order:
      {"kind":"meta","type": clarify|refusal|result}  - as soon as intent is
          known, so the client can pick the right placeholder (a light typing
          indicator for a follow-up question vs. the itinerary card skeleton for
          the final plan — never the ticket skeleton for a mere clarification).
      {"kind":"token","text": "..."}                  - the concierge's reply,
          streamed as the model produces it.
      {"kind":"done", ...ChatResponse fields...}       - the settled turn
          (quick replies for clarify; the full itinerary for a result).
      {"kind":"error","message": "..."}                - a turn-level failure.
    """
    messages = req.messages

    async def event_stream():
        # 1) One fast LLM call classifies intent and extracts trip slots.
        try:
            extraction = await run_in_threadpool(extract_turn, messages)
        except Exception as exc:  # noqa: BLE001 - LLM/transport failure
            yield _sse(
                {
                    "kind": "error",
                    "message": f"Something went wrong understanding your message: {exc}",
                }
            )
            return

        if not extraction.on_topic:
            turn_type = "refusal"
        elif extraction.ready_to_plan:
            turn_type = "result"
        else:
            turn_type = "clarify"
        yield _sse({"kind": "meta", "type": turn_type})

        # 2) Voice the reply, streamed token-by-token. If the streaming call
        #    yields nothing (provider hiccup), fall back to the reply the
        #    extraction step already produced, paced out word-by-word so the
        #    typing effect is identical.
        # Providers (Vertex especially) tend to return a few large chunks, so
        # re-slice each delta into word-sized pieces and pace them for a smooth,
        # even typing effect regardless of how the provider batches.
        streamed: list[str] = []
        iterator = stream_call(build_reply_messages(messages, extraction), temperature=0.7)
        while True:
            try:
                tok = await run_in_threadpool(next, iterator)
            except StopIteration:
                break
            except Exception:
                break

            tok = strip_emoji(tok)
            streamed.append(tok)
            for piece in re.findall(r"\s*\S+", tok):
                yield _sse({"kind": "token", "text": piece})
                await asyncio.sleep(0.012)

        reply_text = "".join(streamed).strip()
        if not reply_text:
            reply_text = extraction.assistant_reply or "Let me help you plan that trip!"
            for piece in re.findall(r"\s*\S+", reply_text):
                yield _sse({"kind": "token", "text": piece})
                await asyncio.sleep(0.02)

        # 3) Refusal / clarify settle immediately; only a result runs the crew.
        if turn_type == "refusal":
            yield _sse({"kind": "done", "type": "refusal", "message": reply_text})
            return

        if turn_type == "clarify":
            yield _sse(
                {
                    "kind": "done",
                    "type": "clarify",
                    "message": reply_text,
                    "quick_replies": [
                        {"label": o, "value": o} for o in extraction.quick_reply_options
                    ],
                }
            )
            return

        # result: build the request, run the crew, persist, emit the itinerary.
        try:
            trip_request = _build_trip_request(extraction.slots)
        except ValidationError as exc:
            yield _sse(
                {
                    "kind": "done",
                    "type": "clarify",
                    "message": (
                        "I need to double check a couple of details before I can "
                        f"plan this trip: {exc}. Could you clarify?"
                    ),
                    "quick_replies": [],
                }
            )
            return

        db_lookup = SessionLocal()
        try:
            previous_itinerary = await run_in_threadpool(
                _fetch_previous_itinerary, db_lookup, messages, req.client_id
            )
        finally:
            db_lookup.close()

        try:
            itinerary, elapsed, _tokens = await run_in_threadpool(
                run_crew, trip_request, previous_itinerary=previous_itinerary
            )
        except Exception as exc:  # noqa: BLE001 - surface a plain, honest failure
            yield _sse(
                {
                    "kind": "error",
                    "message": (
                        "I couldn't generate your itinerary — the planning system "
                        f"hit an error ({exc}). Please try again in a moment."
                    ),
                }
            )
            return

        db = SessionLocal()
        try:
            record = TripRecord(
                system="crew",
                destination=trip_request.destination,
                request=trip_request.model_dump(mode="json"),
                itinerary=itinerary.model_dump(mode="json"),
                within_budget=itinerary.within_budget,
                elapsed_seconds=elapsed,
                client_id=req.client_id,
                **_generation_metadata("crew"),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            record_id = record.id
        finally:
            db.close()

        yield _sse(
            {
                "kind": "done",
                "type": "result",
                "message": reply_text,
                "itinerary": itinerary.model_dump(mode="json"),
                "trip_request": trip_request.model_dump(mode="json"),
                "record_id": record_id,
                "elapsed_seconds": elapsed,
            }
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/itineraries", response_model=ItineraryResponse)
@limiter.limit(_settings.rate_limit_generate)
async def create_itinerary(
    trip_request: TripRequest,
    request: Request,
    system: SystemChoice = Query(
        SystemChoice.crew,
        description="Which generator to use: the multi-agent crew or the baseline.",
    ),
    db: Session = Depends(get_db),
    _api_key: None = Depends(require_api_key),
) -> ItineraryResponse:
    runner = run_crew if system is SystemChoice.crew else run_baseline
    try:
        # Both generators are blocking / CPU+network heavy — run off the loop.
        itinerary, elapsed, _tokens = await run_in_threadpool(runner, trip_request)
    except Exception as exc:  # surface LLM/config errors to the client
        raise HTTPException(status_code=502, detail=f"Generation failed: {exc}")

    record = TripRecord(
        system=system.value,
        destination=trip_request.destination,
        request=trip_request.model_dump(mode="json"),
        itinerary=itinerary.model_dump(mode="json"),
        within_budget=itinerary.within_budget,
        elapsed_seconds=elapsed,
        **_generation_metadata(system.value),
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return ItineraryResponse(
        id=record.id,
        system=system.value,
        elapsed_seconds=elapsed,
        **itinerary.model_dump(),
    )


@app.get("/itineraries/{record_id}", response_model=ItineraryResponse)
def get_itinerary(
    record_id: int,
    db: Session = Depends(get_db),
    _api_key: None = Depends(require_api_key),
) -> ItineraryResponse:
    record = db.get(TripRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Itinerary not found")
    return ItineraryResponse(
        id=record.id,
        system=record.system,
        elapsed_seconds=record.elapsed_seconds,
        **record.itinerary,
    )


@app.get("/itineraries")
def list_itineraries(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _api_key: None = Depends(require_api_key),
) -> list[dict]:
    rows = db.scalars(
        select(TripRecord).order_by(TripRecord.id.desc()).limit(limit)
    ).all()
    return [
        {
            "id": r.id,
            "system": r.system,
            "destination": r.destination,
            "within_budget": r.within_budget,
            "elapsed_seconds": r.elapsed_seconds,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
