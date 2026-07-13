"""ORM models. A single table records every generated trip.

Each row stores the original request, the produced itinerary (as JSON), which
system produced it (`crew` or `baseline`), and how long it took — which also
makes the table a convenient log for the evaluation write-up.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class TripRecord(Base):
    __tablename__ = "trip_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    system: Mapped[str] = mapped_column(String(20), default="crew")
    destination: Mapped[str] = mapped_column(String(200))
    request: Mapped[dict] = mapped_column(JSONB)
    itinerary: Mapped[dict] = mapped_column(JSONB)
    within_budget: Mapped[bool] = mapped_column(default=True)
    elapsed_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Opaque client-generated id (see conversation.ChatRequest.client_id), NOT
    # an auth credential. Scopes "resume/replan my own previous itinerary"
    # lookups to the browser that actually created the record, so one client
    # can't read or silently revise another client's itinerary by guessing/
    # incrementing `id` (see main.py:_fetch_previous_itinerary). Nullable
    # because rows created via the direct /itineraries API (no chat session)
    # have no client_id to scope.
    client_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    # Audit/reproducibility: which model + crew configuration actually
    # produced this row, so a later model/config change doesn't make past
    # generations unexplainable. Populated in main.py.
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generation_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
