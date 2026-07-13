"""Pydantic schemas shared across the whole system.

`TripRequest`  = the user's input (validated by FastAPI).
`Itinerary`    = the structured output produced by BOTH the crew and the
                 single-LLM baseline, so the two can be scored identically.

Keeping one canonical output schema is what makes the baseline-vs-crew
evaluation an apples-to-apples comparison.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator

from .llm import sanitize_untrusted


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------
class TripRequest(BaseModel):
    destination: str = Field(
        ..., min_length=1, max_length=200, description="City or region the user wants to visit"
    )
    origin: str | None = Field(None, max_length=200, description="Where the traveller departs from")
    budget: float = Field(..., gt=0, le=100_000_000, description="Total trip budget")
    currency: str = Field("USD", max_length=10, description="ISO currency code, e.g. USD, EUR")
    start_date: date
    end_date: date
    interests: list[str] = Field(
        default_factory=list,
        max_length=25,
        description="e.g. ['history', 'food', 'hiking', 'museums']",
    )
    travelers: int = Field(1, ge=1, le=50)
    pace: str = Field("balanced", max_length=20, description="relaxed | balanced | packed")
    special_requests: str = Field(
        "",
        max_length=2000,
        description=(
            "Freeform extra instructions the traveller gave beyond the core "
            "slots — e.g. 'add hotel recommendations', 'swap day 2's dinner', "
            "'make day 3 more relaxed'. Threaded into the crew's task prompts "
            "so a replan actually addresses it."
        ),
    )
    discussed_topics: str = Field(
        "",
        max_length=2000,
        description=(
            "A short recap of informational questions the traveller asked "
            "during the conversation (packing, safety, visas, transport, "
            "etc.) that were answered in chat but never fed into a replan — "
            "e.g. 'asked if Alfama is safe at night; asked what to pack for "
            "August'. Threaded into the Reviewer's task so relevant answers "
            "can surface as traveler_notes on the itinerary itself, not just "
            "buried in chat scrollback."
        ),
    )

    @field_validator("end_date")
    @classmethod
    def _end_after_start(cls, v: date, info):
        start = info.data.get("start_date")
        if start and v < start:
            raise ValueError("end_date must be on or after start_date")
        return v

    @field_validator("interests", mode="after")
    @classmethod
    def _cap_interest_length(cls, v: list[str]) -> list[str]:
        return [i[:100] for i in v]

    # Freeform fields get re-embedded verbatim into every downstream agent
    # prompt (see tasks.py's _SPECIAL_REQUEST_NOTE) — sanitize here, at the
    # single point every entry path (chat and the direct API) constructs a
    # TripRequest, rather than at each call site individually.
    @field_validator("special_requests", "discussed_topics", mode="after")
    @classmethod
    def _sanitize_freeform(cls, v: str) -> str:
        return sanitize_untrusted(v)

    @property
    def num_days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    def as_prompt_context(self) -> str:
        """Human-readable one-liner injected into agent/baseline prompts."""
        lines = [
            f"Destination: {self.destination}",
            f"Origin: {self.origin or 'not specified'}",
            f"Dates: {self.start_date} to {self.end_date} ({self.num_days} days)",
            f"Travellers: {self.travelers}",
            f"Total budget: {self.budget} {self.currency}",
            f"Interests: {', '.join(self.interests) or 'general sightseeing'}",
            f"Preferred pace: {self.pace}",
        ]
        if self.special_requests:
            lines.append(f"Additional specific request: {self.special_requests}")
        if self.discussed_topics:
            lines.append(f"Also discussed in chat (answer/acknowledge if relevant): {self.discussed_topics}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output (canonical itinerary schema)
# ---------------------------------------------------------------------------
class Restaurant(BaseModel):
    name: str
    cuisine: str = ""
    price_range: str = Field("", description="e.g. $, $$, $$$")
    note: str = ""


class AccommodationOption(BaseModel):
    name: str
    area: str = ""
    price_range: str = Field("", description="e.g. $, $$, $$$ or a per-night estimate")
    note: str = ""


class DayPlan(BaseModel):
    day: int
    title: str = ""
    morning: str = ""
    afternoon: str = ""
    evening: str = ""
    estimated_cost: float = 0.0


class BudgetBreakdown(BaseModel):
    accommodation: float = 0.0
    food: float = 0.0
    activities: float = 0.0
    transport: float = 0.0
    misc: float = 0.0
    total_estimated: float = 0.0
    currency: str = "USD"
    within_budget: bool = True
    notes: str = ""


class Itinerary(BaseModel):
    destination: str
    summary: str = ""
    num_days: int = 0
    attractions: list[str] = Field(default_factory=list)
    restaurants: list[Restaurant] = Field(default_factory=list)
    personalization_notes: str = ""
    prioritize: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    accommodation_options: list[AccommodationOption] = Field(default_factory=list)
    transportation: list[str] = Field(default_factory=list)
    daily_plans: list[DayPlan] = Field(default_factory=list)
    budget: BudgetBreakdown = Field(default_factory=BudgetBreakdown)
    within_budget: bool = True
    traveler_notes: list[str] = Field(
        default_factory=list,
        description=(
            "Short bullets answering/acknowledging specific things the "
            "traveller asked about during the conversation (e.g. safety, "
            "packing, a day-trip idea) that don't fit any other field. Only "
            "populated when there's real substance to surface — not a "
            "generic restatement of the itinerary."
        ),
    )
    reviewer_notes: str = ""
