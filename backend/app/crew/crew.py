"""Assemble and run the Travel Concierge crew.

Process = SEQUENTIAL: Destination -> Food -> Personalization -> Accommodation ->
Budget -> Planner -> Reviewer. Sequential is deliberately chosen over
hierarchical delegation for two reasons: it is more deterministic and makes far
fewer LLM calls (keeping runs comfortably inside free-tier rate limits), and —
verified live — CrewAI's hierarchical process routes every task through one
shared manager-agent executor that is not reentrant, so running the five
research tasks concurrently under hierarchical raises "Executor is already
running"; only the sequential process's independent per-agent executors support
`async_execution`. Shared state between agents is provided by task `context`
chaining (see tasks.py).
"""
from __future__ import annotations

import re
import time
import unicodedata
from datetime import date, timedelta

from crewai import Crew, Process

from ..schemas import BudgetBreakdown, Itinerary, Source, TripRequest
from ..llm import build_llm
from .agents import build_agents
from .manifest import STEP_KEYS
from .progress import (
    CURRENT_JOB,
    CURRENT_SINK,
    JobCancelled,
    ProgressSink,
    make_step_callback,
    make_task_callback,
)
from .sources import CURRENT_SOURCES, SourceRegistry
from .tasks import build_tasks
from .tools import choose_tier, compute_budget, warm_knowledge_base

# Research steps all start together at kickoff; used as a fallback so the
# checklist flips them to "running" even if the (optional) event bus is silent.
_RESEARCH_KEYS = ["destination", "food", "personalization", "accommodation", "budget"]


def build_crew(
    verbose: bool = False,
    *,
    rag_enabled: bool | None = None,
    memory: bool | None = None,
    process: str | None = None,
    progress: ProgressSink | None = None,
) -> Crew:
    """Assemble the crew. The three keyword toggles default to `.env`
    (config.py) and exist so the evaluation harness can run controlled
    variants — a RAG-ablation, a hierarchical topology, or persistent memory —
    without editing code. Defaults reproduce the report's configuration.
    """
    from ..config import get_settings

    settings = get_settings()
    rag_enabled = settings.rag_enabled if rag_enabled is None else rag_enabled
    memory = settings.crew_memory if memory is None else memory
    process_name = (process or settings.crew_process).lower().strip()
    hierarchical = process_name == "hierarchical"

    if rag_enabled:
        # Load the embedding model once, up front, on this thread — several
        # agents will call the knowledge_base tool concurrently once kickoff()
        # starts, and racing the first-ever load against that concurrent
        # activity has been observed to crash the process (see warm_knowledge_base).
        warm_knowledge_base()

    agents = build_agents(rag_enabled=rag_enabled)
    # Hierarchical delegation is driven by the manager, not by static async
    # siblings, so async execution is only used under the sequential process.
    tasks = build_tasks(agents, allow_async=not hierarchical)

    kwargs: dict = dict(
        agents=list(agents.values()),
        tasks=tasks,
        # Throttle the crew's own LLM calls below the free-tier RPM ceiling so a
        # burst of agent/tool calls doesn't trip a 429 mid-run.
        max_rpm=settings.max_rpm,
        verbose=verbose,
    )

    # Progress callbacks are attached ONLY when a sink is supplied, so a run
    # with `progress=None` (evaluation, direct callers) is byte-for-byte the old
    # behaviour. task_callback = checklist state; step_callback = cancel checks.
    if progress is not None:
        kwargs["task_callback"] = make_task_callback(progress)
        kwargs["step_callback"] = make_step_callback(progress)

    if hierarchical:
        # A manager agent plans and delegates to the six specialists (genuine
        # delegation, at the cost of extra manager LLM calls and non-
        # deterministic ordering). CrewAI creates the manager from manager_llm.
        kwargs["process"] = Process.hierarchical
        kwargs["manager_llm"] = build_llm(model=settings.manager_model)
    else:
        kwargs["process"] = Process.sequential

    if memory:
        # Persistent long-term memory across runs, embedded with the SAME local
        # sentence-transformers model the RAG store uses — the "sentence-
        # transformer" provider runs the model locally (no external key, unlike
        # CrewAI's "huggingface" provider which calls the HF inference API).
        # Stored under CrewAI's default location.
        kwargs["memory"] = True
        kwargs["embedder"] = {
            "provider": "sentence-transformer",
            "config": {
                "model_name": settings.embedding_model.split("/")[-1],
            },
        }
    else:
        # `memory=False` keeps the demo dependency-free and each generation
        # independent. Inter-agent state is still realised via task-context
        # chaining (see tasks.py); this only governs *persistent* memory.
        kwargs["memory"] = False

    return Crew(**kwargs)


def _format_previous_itinerary(itinerary: Itinerary) -> str:
    """Render a previous Itinerary as compact text the Planner/Reviewer can
    copy from verbatim for anything the special request doesn't touch.

    Deliberately plain and complete rather than a summary — the whole point
    is giving the agents an exact baseline to preserve, not a paraphrase they
    might drift from.
    """
    lines = [f"Summary: {itinerary.summary}"] if itinerary.summary else []
    for d in itinerary.daily_plans:
        lines.append(
            f"Day {d.day}: {d.title}\n"
            f"  Morning: {d.morning}\n"
            f"  Afternoon: {d.afternoon}\n"
            f"  Evening: {d.evening}\n"
            f"  Estimated cost: {d.estimated_cost}"
        )
    if itinerary.attractions:
        lines.append("Attractions: " + "; ".join(itinerary.attractions))
    if itinerary.restaurants:
        lines.append(
            "Restaurants: "
            + "; ".join(f"{r.name} ({r.cuisine}, {r.price_range})" for r in itinerary.restaurants)
        )
    if itinerary.accommodation_options:
        lines.append(
            "Accommodation options: "
            + "; ".join(f"{a.name} ({a.area}, {a.price_range})" for a in itinerary.accommodation_options)
        )
    if itinerary.transportation:
        lines.append("Transportation: " + "; ".join(itinerary.transportation))
    return "\n".join(lines) if lines else "none"


def _weekday_context(start_date: date, num_days: int) -> str:
    """Day-of-week for each day of the trip, e.g. 'Day 1 = Monday (2027-04-05);
    Day 2 = Tuesday (2027-04-06)'.

    Computed here rather than asked of the LLM (this codebase's "LLM
    extracts, code derives" convention — see start_month/end_month below and
    CLAUDE.md) because a model reliably gets weekday arithmetic wrong from a
    date string. An external judge audit caught a real itinerary that
    scheduled two normally Monday-closed Paris museums on a day that was, in
    fact, a Monday — no tool or prompt gave any agent the actual weekday to
    reason about. This doesn't verify real opening hours (that needs a
    hours-aware lookup, not yet wired up), but it's enough for an agent to at
    least flag "this is commonly a closure day" instead of confidently
    scheduling a specific venue on a day it can't verify is open.
    """
    return "; ".join(
        f"Day {i + 1} = {(start_date + timedelta(days=i)).strftime('%A')} "
        f"({(start_date + timedelta(days=i)).isoformat()})"
        for i in range(max(num_days, 1))
    )


def _inputs_from_request(
    request: TripRequest, previous_itinerary: Itinerary | None = None, traveller_memory: str | None = None
) -> dict:
    """Flatten a TripRequest into the placeholder dict the tasks expect."""
    return {
        "destination": request.destination,
        "origin": request.origin or "not specified",
        "num_days": request.num_days,
        "travelers": request.travelers,
        "budget": request.budget,
        "currency": request.currency,
        "interests": ", ".join(request.interests) or "general sightseeing",
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
        # Month-granularity for flight_price_lookup: empirically, Travelpayouts'
        # cache (real searches from the last 48h) almost never has a hit at
        # exact day-level for a specific route, but reliably does at
        # month-level — computed here rather than asking the agent to truncate
        # a date string itself (this codebase's "LLM extracts, code derives"
        # convention — see CLAUDE.md).
        "start_month": request.start_date.strftime("%Y-%m"),
        "end_month": request.end_date.strftime("%Y-%m"),
        "weekday_context": _weekday_context(request.start_date, request.num_days),
        "pace": request.pace,
        "special_requests": request.special_requests or "none",
        "discussed_topics": request.discussed_topics or "none",
        "previous_itinerary": (
            _format_previous_itinerary(previous_itinerary) if previous_itinerary else "none"
        ),
        # Only ever set for a memory_opt_in user with at least one prior
        # trip — see delivery/memory.py. "none" (not empty string) so the
        # placeholder always resolves to something the prompt reads sensibly.
        "traveller_memory": traveller_memory or "none",
    }


def run_crew(
    request: TripRequest,
    verbose: bool = False,
    *,
    rag_enabled: bool | None = None,
    memory: bool | None = None,
    process: str | None = None,
    previous_itinerary: Itinerary | None = None,
    progress: ProgressSink | None = None,
    job_id: str | None = None,
    traveller_memory: str | None = None,
) -> tuple[Itinerary, float, int]:
    """Run the full crew. Returns (itinerary, elapsed_seconds, total_tokens).

    The optional toggles forward to `build_crew` so callers (e.g. the eval
    harness) can request a controlled variant — RAG-ablation, hierarchical
    topology, or persistent memory — without touching `.env`.

    `previous_itinerary`, when given, is a prior generation for the SAME trip
    that the caller wants revised rather than rebuilt from scratch (a
    confirmed chat follow-up — see `main.py`). It is not part of `TripRequest`
    itself so evaluation/baseline callers are unaffected; only the Planner and
    Reviewer tasks see it, with explicit instructions to preserve everything
    it contains except what `request.special_requests` calls out.

    `traveller_memory`, when given, is a short summary of this user's past
    trips (see delivery/memory.py) — the caller (queue/jobs.py) is
    responsible for only ever passing this for a memory_opt_in user; run_crew
    itself does no opt-in check, it just threads whatever string it's handed
    to the Personalization task.
    """
    inputs = _inputs_from_request(request, previous_itinerary, traveller_memory)
    start = time.perf_counter()

    # Route this run's progress: set the ContextVars so the (global) event bus
    # handlers — and the async research threads CrewAI spawns via copy_context()
    # — publish to THIS job's sink and no other.
    sink_token = CURRENT_SINK.set(progress) if progress is not None else None
    job_token = CURRENT_JOB.set(job_id) if job_id is not None else None
    # A fresh citation registry per run, always (not gated on `progress`) — a
    # grounded tool call registers into it regardless of whether this run is
    # job-tracked, and evaluation callers benefit from the same citations.
    source_registry = SourceRegistry()
    sources_token = CURRENT_SOURCES.set(source_registry)
    if progress is not None:
        # Flip the five research steps to "running" up front — they all start
        # together at kickoff, and this keeps the checklist alive even if the
        # optional event bus is silent on this CrewAI version.
        for key in _RESEARCH_KEYS:
            try:
                progress.step(key, "running")
            except Exception:  # noqa: BLE001
                pass

    # Retry policy differs by path. When a progress sink is supplied the run is
    # owned by the arq worker, which owns retries (observable, bounded, and
    # survives a worker crash) — so run kickoff ONCE here to avoid 2x2 = 4
    # kickoffs. The classic in-process 2x retry is kept ONLY for the
    # progress=None path so evaluation behaviour is bit-identical.
    attempts = 1 if progress is not None else 2
    last_exc: Exception | None = None
    result = crew = None
    try:
        for attempt in range(attempts):
            crew = build_crew(
                verbose=verbose,
                rag_enabled=rag_enabled,
                memory=memory,
                process=process,
                progress=progress,
            )
            try:
                result = crew.kickoff(inputs=inputs)
                break
            except JobCancelled:
                # Not a retryable failure — a cooperative cancel (raised from
                # step_callback via check_cancelled(), see progress.py) that
                # propagated up through crew.kickoff(). CrewAI's own exception
                # handling re-raises it unchanged (confirmed by reading
                # agent_executor.py / flow/runtime — both do a bare `raise`
                # after logging it as a scary-looking "unknown error"), but the
                # broad `except Exception` below would otherwise catch it here,
                # discard its type, and report it via the generic
                # `RuntimeError(f"Crew failed after retries: {last_exc}")` path
                # below — which is exactly why every cancel in testing showed
                # up as "Something went wrong" instead of "This plan was
                # cancelled": run_itinerary_job's `except JobCancelled` never
                # got a chance to match. Re-raise immediately so it does.
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < attempts - 1:
                    time.sleep(2)
    finally:
        CURRENT_SOURCES.reset(sources_token)
        if job_token is not None:
            CURRENT_JOB.reset(job_token)
        if sink_token is not None:
            CURRENT_SINK.reset(sink_token)
    if result is None:
        raise RuntimeError(f"Crew failed after retries: {last_exc}")
    elapsed = time.perf_counter() - start
    # Total tokens consumed across all agents/calls (resource-utilisation metric).
    usage = getattr(crew, "usage_metrics", None)
    total_tokens = int(getattr(usage, "total_tokens", 0) or 0)

    itinerary: Itinerary | None = getattr(result, "pydantic", None)
    if itinerary is None:
        # Fallback: the model returned text/JSON we must coerce into the schema.
        raw = getattr(result, "json_dict", None) or {}
        itinerary = Itinerary(destination=request.destination, **raw) if raw else Itinerary(
            destination=request.destination,
            summary=str(result),
        )

    # Backfill/repair fields the LLM may have left inconsistent.
    if not itinerary.num_days:
        itinerary.num_days = request.num_days

    # Reattach citations from the registry AFTER kickoff — the agents only
    # ever saw short [s1]-style handles (see sources.py), never the URLs
    # themselves, so this is the one place full Source objects get assembled.
    itinerary.sources = source_registry.all()
    _strip_invalid_source_ids(itinerary, source_registry, request.destination)
    _backfill_missing_source_ids(itinerary, source_registry, request.destination)
    _backfill_flight_note(itinerary, source_registry.flight_note)

    if previous_itinerary is not None:
        _backfill_dropped_fields(itinerary, previous_itinerary)

    _reconcile_budget(itinerary, request, flight_note=source_registry.flight_note)
    return itinerary, elapsed, total_tokens


def _backfill_flight_note(itinerary: Itinerary, flight_note: str | None) -> None:
    """Deterministically ensure a real flight_price_lookup hit survives into
    the final itinerary. The Destination agent reliably writes the note into
    its own draft, and the Planner reliably folds it into its prose "Transport
    Suggestions" — but the Reviewer's final JSON reconstruction pass (a single
    LLM call rebuilding the whole Itinerary object) has been observed to
    silently drop it from `transportation` even though nothing else about the
    entry needed to change (verified live: "Flights: $557 (from New York)"
    present in the Planner's draft, absent from the Reviewer's structured
    output). Same class of one-JSON-pass drift `_reconcile_budget` and
    `_backfill_dropped_fields` already correct for elsewhere in this file.
    Only ever ADDS the captured note if transportation doesn't already
    mention a flight; never invents a price itself.
    """
    if not flight_note:
        return
    if any("flight" in t.lower() for t in itinerary.transportation):
        return
    itinerary.transportation.insert(0, flight_note)


# Generic hospitality/travel words carry no identifying signal on their own
# (e.g. "Hotel" or "Paris" appears in nearly every source title on a Paris
# trip) — excluded so they can't drive a false-positive word match between
# two genuinely different venues that just happen to share a generic word.
_GENERIC_VENUE_WORDS = {
    "hotel", "hotels", "hostel", "cafe", "cafes", "restaurant", "restaurants",
    "museum", "museums", "the", "and", "de", "des", "du", "la", "le", "les", "et",
}


def _venue_words(text: str, extra_stopwords: frozenset[str] = frozenset()) -> set[str]:
    """Significant (>=4 char, non-generic) normalized word tokens in `text` —
    strips accents ('Hôtel' -> 'Hotel') so minor wording differences between
    an LLM's own phrasing and a tool's returned title don't cause a false
    mismatch. `extra_stopwords` additionally excludes words that carry no
    identifying signal in THIS trip's context (see `_source_names_venue`)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_text.lower())
    return {
        w for w in words
        if len(w) >= 4 and w not in _GENERIC_VENUE_WORDS and w not in extra_stopwords
    }


def _source_names_venue(
    source: Source, venue_name: str, extra_stopwords: frozenset[str] = frozenset()
) -> bool:
    """Does this Source's title plausibly refer to `venue_name`?

    Word-set containment, not a substring or exact-equality check — a plain
    substring match breaks on word-order differences alone (verified live:
    "Hotel Britannique" vs. the real Google Places title "Britannique Hotel -
    Paris Centre" is a genuine match, but "hotelbritannique" is not a
    substring of "britanniquehotelpariscentre"). Requiring the FULL set of
    the venue's significant words to appear in the title (not just any one)
    is what keeps this from producing a false positive on a shared generic
    word — e.g. "Angelina Paris" wrongly cited to a "Paris café culture"
    history article shares only "Paris", which isn't enough on its own.

    `extra_stopwords` should be the trip's destination — verified live:
    without it, the genuinely correct citation "Angelina" (Google Places'
    own title, which naturally doesn't repeat the city name) failed to match
    the agent's own "Angelina Paris" purely because "Paris" isn't part of
    the source's title. The destination is uninformative for matching
    purposes the same way "Hotel" or "Cafe" is — it appears in nearly every
    source on the trip, so it shouldn't be required to line up.

    A citation that looks grounded but silently isn't is worse than no
    citation at all — same "code enforces the invariant, the model is
    best-effort" pattern as `_reconcile_budget`.
    """
    venue_words = _venue_words(venue_name, extra_stopwords)
    if not venue_words:
        return False
    return venue_words <= _venue_words(source.title, extra_stopwords)


def _normalize_source_id(raw: str) -> str:
    """A bare digit ("7") is treated as shorthand for its "sN" handle.

    Verified live: despite the task instructions spelling out the format
    with an example ("copy that exact handle... e.g. 's7'"), an agent
    dropped the 's' prefix for EVERY citation in its own draft ("source_ids:
    7, 8, 10, 11, 12"), not just in the Reviewer's transcription of it — a
    formatting quirk prompt wording alone didn't reliably prevent. Without
    this, `_strip_invalid_source_ids` would silently discard every one of
    those citations as "not in the registry", even when the underlying
    handle is genuinely valid and about the right venue.
    """
    raw = raw.strip()
    return f"s{raw}" if raw.isdigit() else raw


def _strip_invalid_source_ids(
    itinerary: Itinerary, source_registry: SourceRegistry, destination: str
) -> None:
    """Drop any `source_ids` handle that doesn't genuinely back its claim.

    Three distinct failure modes, all verified live:
      1. A fabricated handle (e.g. "s0") the registry (1-indexed, see
         sources.py) never assigned — this alone was the original fix.
      2. A REAL, registered handle attached to the wrong venue (e.g. a
         restaurant citing the NYC->PAR flight-price source, or a generic
         blog post about a different topic) — an existence check alone
         doesn't catch this, since the id is genuinely valid, just wrong.
      3. A real handle missing its "s" prefix (see `_normalize_source_id`).
    A citation that looks grounded but silently isn't is worse than no
    citation at all — same "code enforces the invariant, the model is
    best-effort" pattern as `_reconcile_budget`, now covering all three.
    `destination` is excluded from the name match (see `_source_names_venue`)
    since it's uninformative noise shared by nearly every source on the trip.
    """
    stopwords = frozenset(_venue_words(destination))
    by_id = {s.id: s for s in source_registry.all()}
    for restaurant in itinerary.restaurants:
        normalized = (_normalize_source_id(sid) for sid in restaurant.source_ids)
        restaurant.source_ids = [
            sid for sid in normalized
            if sid in by_id and _source_names_venue(by_id[sid], restaurant.name, stopwords)
        ]
    for option in itinerary.accommodation_options:
        normalized = (_normalize_source_id(sid) for sid in option.source_ids)
        option.source_ids = [
            sid for sid in normalized
            if sid in by_id and _source_names_venue(by_id[sid], option.name, stopwords)
        ]


def _backfill_missing_source_ids(
    itinerary: Itinerary, source_registry: SourceRegistry, destination: str, *, max_ids: int = 3
) -> None:
    """Auto-attach a citation the model left blank, when an unambiguous match
    exists in the registry — run strictly AFTER `_strip_invalid_source_ids`,
    and only ever fills a gap, never overwrites an id the model did attempt.

    Verified live: once `_strip_invalid_source_ids` started correctly
    rejecting wrong-venue citations, the model swung the other way and
    started leaving `source_ids` empty even for venues with an exact-title
    match sitting right in its own context that run (e.g. "Angelina" left
    uncited despite a Source titled exactly "Angelina" from the same run's
    `places_lookup` call) — real recall lost to the precision fix. The right
    fix is code doing the safe, deterministic lookup itself rather than
    prompting the model to be more assertive about a judgment call — the
    weekday-closure note in tasks.py was escalated the same way (a soft note,
    then a "REQUIRED CHECK") and never worked reliably across three separate
    live runs, so more prompt wording isn't the lever to pull here.
    `_source_names_venue`'s word-set-containment match is strict enough
    (requires ALL of a venue's significant words to appear in the source
    title) that multiple matches for one venue name almost always mean
    multiple sources about the SAME place (e.g. a Google Places result plus
    several review-site hits) rather than a true collision between two
    different venues — so this attaches up to `max_ids` matches, not just a
    single "unique match only" result. `destination` is excluded from the
    name match for the same reason `_strip_invalid_source_ids` excludes it —
    see `_source_names_venue`.
    """
    stopwords = frozenset(_venue_words(destination))
    sources = source_registry.all()
    for restaurant in itinerary.restaurants:
        if not restaurant.source_ids:
            restaurant.source_ids = [
                s.id for s in sources if _source_names_venue(s, restaurant.name, stopwords)
            ][:max_ids]
    for option in itinerary.accommodation_options:
        if not option.source_ids:
            option.source_ids = [
                s.id for s in sources if _source_names_venue(s, option.name, stopwords)
            ][:max_ids]


def _backfill_dropped_fields(itinerary: Itinerary, previous_itinerary: Itinerary) -> None:
    """Restore day fields the revision dropped despite the "preserve
    everything untouched" instruction (verified live: a real run correctly
    applied a single-day special request but came back with every day's
    `afternoon` field emptied, on both changed and unchanged days — the
    larger prompt from adding previous-itinerary context measurably degrades
    completeness). Only fills fields that are BLANK in the new output but had
    content in the previous one; never overwrites a field the revision
    actually filled in (including a field that was intentionally changed),
    so a genuine edit — even one that shortens or removes a plan — is never
    clobbered by this backstop.
    """
    previous_by_day = {d.day: d for d in previous_itinerary.daily_plans}
    for day in itinerary.daily_plans:
        prev = previous_by_day.get(day.day)
        if prev is None:
            continue
        if not day.morning.strip() and prev.morning.strip():
            day.morning = prev.morning
        if not day.afternoon.strip() and prev.afternoon.strip():
            day.afternoon = prev.afternoon
        if not day.evening.strip() and prev.evening.strip():
            day.evening = prev.evening
        if not day.title.strip() and prev.title.strip():
            day.title = prev.title
        if not day.estimated_cost and prev.estimated_cost:
            day.estimated_cost = prev.estimated_cost


def _reconcile_budget(
    itinerary: Itinerary, request: TripRequest, *, flight_note: str | None = None
) -> None:
    """Deterministically enforce budget INVARIANTS on the crew's output.

    We deliberately keep the LLM's per-category figures — they reflect the
    *actual* itinerary it designed (e.g. a fine-dining night pushes 'food' up).
    In code we only guarantee the things that must be arithmetically true,
    which LLMs routinely get wrong when transcribing a final object:
      1. total_estimated == sum(categories)          -> internal consistency
      2. within_budget   == (total <= user budget)    -> a correct verdict
      3. notes            == a prose restatement of (1) and (2), plus a
                              disclosure when a flight cost is quoted
                              elsewhere but not part of that total
    If the model left the budget empty, fall back to the deterministic tool.
    The single-LLM baseline gets none of this, which is exactly the advantage
    the Budget agent + tool are meant to confer.

    (3) matters as much as (1)/(2): an external LLM-judge audit (see
    evaluation notes) caught a real case where `notes` stated "Accommodation:
    270.0 USD ... Total: 660.0 USD" while the structured `accommodation` field
    was actually 0.0 and the reconciled total was 390.0 — the LLM's prose had
    gone stale relative to fields (1)/(2) already fix, because nothing
    regenerated it afterward. `notes` is now always rebuilt from the final,
    reconciled numbers rather than trusted from the model, so the two can
    never again disagree.

    The `flight_note` disclosure closes a related gap the same audit caught:
    `_backfill_flight_note` inserts a real flight price into `transportation`
    as prose, entirely independent of this function's category sum — so a
    traveller could see "Flights: ~$557" in Transportation and "$660 total,
    within budget" in Budget with no indication the $557 isn't in that $660.
    `estimate_budget`'s categories were never designed to include airfare (it
    prices a destination-local trip), so the fix is disclosure, not silently
    folding an unvalidated dollar figure into a budget category it doesn't
    belong to.
    """
    b = itinerary.budget or BudgetBreakdown()
    category_sum = round(
        b.accommodation + b.food + b.activities + b.transport + b.misc, 2
    )
    if category_sum <= 0:
        # Model produced no usable budget — use the deterministic estimator.
        tier = choose_tier(request.num_days, request.travelers, request.budget)
        d = compute_budget(request.num_days, request.travelers, request.budget, tier)
        b = BudgetBreakdown(
            accommodation=d["accommodation"], food=d["food"],
            activities=d["activities"], transport=d["transport"], misc=d["misc"],
            total_estimated=d["total_estimated"],
        )
        category_sum = d["total_estimated"]

    b.total_estimated = category_sum
    b.currency = request.currency
    b.within_budget = category_sum <= request.budget
    b.notes = (
        f"Estimated cost breakdown for {request.num_days} day(s), {request.travelers} "
        f"traveler(s): Accommodation {b.accommodation:.2f} {b.currency}, "
        f"Activities {b.activities:.2f} {b.currency}, Food {b.food:.2f} {b.currency}, "
        f"Transport {b.transport:.2f} {b.currency}, Miscellaneous {b.misc:.2f} {b.currency}. "
        f"Total estimated cost: {b.total_estimated:.2f} {b.currency} against a budget of "
        f"{request.budget:.2f} {b.currency} — "
        f"{'within budget' if b.within_budget else 'over budget'}."
    )
    if flight_note:
        b.notes += f" Note: this total excludes airfare — see \"{flight_note}\" in Transportation."
    itinerary.budget = b
    itinerary.within_budget = b.within_budget
