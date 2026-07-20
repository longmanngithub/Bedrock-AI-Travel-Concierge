"""Per-run citation registry.

Design constraint: a URL must never round-trip through an LLM token stream —
models paraphrase, truncate, and invent URLs. Instead, each grounded tool call
registers its result here and hands the AGENT only a short handle (`[s1]`);
`crew.py::run_crew` reattaches the full `Source` objects to the final
`Itinerary` AFTER `kickoff()`, from this registry, as a post-processing step
alongside `_reconcile_budget`.

Threading: CrewAI's `async_execution` research tasks run in separate threads,
but `Task.execute_async` calls `contextvars.copy_context()` before starting
each one (verified against crewai/task.py), so a `ContextVar` set before
`kickoff()` is visible inside every research thread — the same mechanism
`crew/progress.py` relies on for per-job progress routing. One registry
instance per crew run; `_lock` guards concurrent `add()` calls from the five
parallel research threads.
"""
from __future__ import annotations

import itertools
import threading
from contextvars import ContextVar
from datetime import datetime, timezone

from ..schemas import Source


class SourceRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, Source] = {}
        self._counter = itertools.count(1)
        self._lock = threading.Lock()
        # A real flight_price_lookup hit, captured verbatim at the point the
        # tool call finds one (see crew/tools.py) — not re-derived from
        # agent prose. crew.py reads this post-kickoff to deterministically
        # backfill `transportation` if the multi-hop LLM chain (Destination ->
        # Planner -> Reviewer) drops the note along the way, same as
        # _reconcile_budget backstops budget arithmetic.
        self.flight_note: str | None = None

    def add(self, *, title: str, url: str, publisher: str) -> str:
        """Register a grounded result and return its short citation handle."""
        with self._lock:
            sid = f"s{next(self._counter)}"
            self._sources[sid] = Source(
                id=sid,
                title=(title or url)[:200],
                url=url,
                publisher=publisher[:100],
                retrieved_at=datetime.now(timezone.utc).isoformat(),
            )
        return sid

    def set_flight_note(self, text: str) -> None:
        with self._lock:
            self.flight_note = text

    def all(self) -> list[Source]:
        with self._lock:
            return list(self._sources.values())


CURRENT_SOURCES: ContextVar[SourceRegistry | None] = ContextVar("crew_sources", default=None)


def current_registry() -> SourceRegistry | None:
    """None outside a tracked run (e.g. evaluation calling run_crew directly
    without wanting citations) — every call site must handle that by falling
    back to an uncited result, never by erroring."""
    return CURRENT_SOURCES.get()
