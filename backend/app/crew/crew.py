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

import time

from crewai import Crew, Process

from ..schemas import BudgetBreakdown, Itinerary, TripRequest
from ..llm import build_llm
from .agents import build_agents
from .tasks import build_tasks
from .tools import choose_tier, compute_budget, warm_knowledge_base


def build_crew(
    verbose: bool = False,
    *,
    rag_enabled: bool | None = None,
    memory: bool | None = None,
    process: str | None = None,
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


def _inputs_from_request(
    request: TripRequest, previous_itinerary: Itinerary | None = None
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
        "pace": request.pace,
        "special_requests": request.special_requests or "none",
        "discussed_topics": request.discussed_topics or "none",
        "previous_itinerary": (
            _format_previous_itinerary(previous_itinerary) if previous_itinerary else "none"
        ),
    }


def run_crew(
    request: TripRequest,
    verbose: bool = False,
    *,
    rag_enabled: bool | None = None,
    memory: bool | None = None,
    process: str | None = None,
    previous_itinerary: Itinerary | None = None,
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
    """
    inputs = _inputs_from_request(request, previous_itinerary)
    start = time.perf_counter()
    # Retry the whole crew on a transient failure (e.g. an occasional empty LLM
    # response), so a one-off provider hiccup doesn't fail the request.
    last_exc: Exception | None = None
    result = crew = None
    for attempt in range(2):
        crew = build_crew(
            verbose=verbose,
            rag_enabled=rag_enabled,
            memory=memory,
            process=process,
        )
        try:
            result = crew.kickoff(inputs=inputs)
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(2)
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

    if previous_itinerary is not None:
        _backfill_dropped_fields(itinerary, previous_itinerary)

    _reconcile_budget(itinerary, request)
    return itinerary, elapsed, total_tokens


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


def _reconcile_budget(itinerary: Itinerary, request: TripRequest) -> None:
    """Deterministically enforce budget INVARIANTS on the crew's output.

    We deliberately keep the LLM's per-category figures — they reflect the
    *actual* itinerary it designed (e.g. a fine-dining night pushes 'food' up).
    In code we only guarantee the two things that must be arithmetically true,
    which LLMs routinely get wrong when transcribing a final object:
      1. total_estimated == sum(categories)          -> internal consistency
      2. within_budget   == (total <= user budget)    -> a correct verdict
    If the model left the budget empty, fall back to the deterministic tool.
    The single-LLM baseline gets none of this, which is exactly the advantage
    the Budget agent + tool are meant to confer.
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
            notes=f"Deterministic {tier}-tier estimate (model gave no budget).",
        )
        category_sum = d["total_estimated"]

    b.total_estimated = category_sum
    b.currency = request.currency
    b.within_budget = category_sum <= request.budget
    itinerary.budget = b
    itinerary.within_budget = b.within_budget
