"""Agent memory: a rolling per-user preference summary, built from past trips
and injected into the Personalization agent's task context.

Only ever read or written when `User.memory_opt_in` is True — callers
(queue/jobs.py) are responsible for that check; this module doesn't
re-check it, so it stays a pure "given a user_id, do the thing" utility
that's trivial to unit-test and impossible to accidentally call for an
opted-out user by forgetting a flag deep inside it.

Deliberately NOT an LLM-derived summary — no extra model call, no cost, no
extra failure mode. Just the two signals honestly extractable from a
TripRequest without inference: the union of interests mentioned across
trips, and a running average of budget-per-day. That's enough to bias the
Personalization agent ("this traveller tends to like X, Y") without
overclaiming precision the data doesn't support (e.g. no attempt to infer
"avoided things" from special_requests text — that would need an LLM call
of its own and a real false-positive risk).
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..llm import sanitize_untrusted
from ..models import UserPreference
from ..schemas import TripRequest

_MAX_INTERESTS = 15


def build_traveller_memory(user_id: uuid.UUID, db: Session) -> str | None:
    """Returns a short prompt-ready summary, or None if there's nothing
    learned yet (first trip, or the user opted in only just now)."""
    pref = db.scalar(select(UserPreference).where(UserPreference.user_id == user_id))
    if pref is None or pref.trip_count == 0:
        return None

    bits = []
    if pref.interests:
        bits.append("has previously shown interest in: " + ", ".join(pref.interests))
    if pref.pace:
        bits.append(f"usually travels at a {pref.pace} pace")
    if pref.avg_budget_per_day:
        bits.append(f"typically budgets around {pref.avg_budget_per_day:.0f}/day")
    if not bits:
        return None
    plural = "trip" if pref.trip_count == 1 else "trips"
    summary = f"Based on {pref.trip_count} previous {plural}, this traveller " + "; ".join(bits) + "."
    # `interests` is raw user free-text persisted across trips (see
    # update_preferences_after_trip) and replayed into the Personalization
    # prompt every future trip — unlike special_requests/discussed_topics
    # (sanitized on the way in via schemas.py's _sanitize_freeform) or tool
    # results (crew/tools.py's _wrap_untrusted), it never passed through the
    # injection-phrase backstop, so apply it here at the point this summary
    # becomes prompt material.
    return sanitize_untrusted(summary)


def update_preferences_after_trip(user_id: uuid.UUID, trip_request: TripRequest, db: Session) -> None:
    """Upserts the rolling summary after a SUCCESSFUL crew run. Call this
    only once the itinerary is actually built — a trip that failed mid-crew
    taught us nothing real about the traveller."""
    pref = db.scalar(select(UserPreference).where(UserPreference.user_id == user_id))
    if pref is None:
        # `interests=[]`/`trip_count=0` explicitly — the columns' ORM
        # defaults are flush-time defaults, so a freshly-constructed row's
        # attributes are still None until committed, and the arithmetic
        # below would crash on them.
        pref = UserPreference(user_id=user_id, interests=[], trip_count=0)
        db.add(pref)

    merged = list(dict.fromkeys([*pref.interests, *trip_request.interests]))  # dedupe, preserve order
    pref.interests = merged[-_MAX_INTERESTS:]  # keep the most recent signal if it grows unbounded
    pref.pace = trip_request.pace

    per_day = trip_request.budget / max(trip_request.num_days, 1)
    if pref.avg_budget_per_day is None:
        pref.avg_budget_per_day = per_day
    else:
        # Running average weighted by trip count so one unusually
        # extravagant/frugal trip doesn't overwrite the established signal.
        pref.avg_budget_per_day = (pref.avg_budget_per_day * pref.trip_count + per_day) / (pref.trip_count + 1)
    pref.trip_count += 1
    db.commit()
