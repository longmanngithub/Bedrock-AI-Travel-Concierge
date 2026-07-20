"""Model tiering — the "cheapest capable model per task" mechanism.

Three tiers (`cheap` / `standard` / `quality`) each map to a LiteLLM
`provider/model` string, and each *task* (extraction, reply, answer, research,
planner, reviewer, judge) maps to a tier via a config string. This is what lets
different jobs run different models — possibly from different providers — while
`build_llm()` stays the single entry point.

Backwards compatibility is load-bearing: the legacy `model` / `quality_model` /
`judge_model` settings still work by *seeding* the tiers when the new tier vars
are unset, so every existing `.env`, `docker-compose.prod.yml`, and the report's
reproducibility claims keep producing the same models.
"""
from __future__ import annotations

from enum import Enum
from functools import lru_cache

from .config import get_settings


class Tier(str, Enum):
    cheap = "cheap"
    standard = "standard"
    quality = "quality"


# Default task -> tier assignment, overridable via Settings.task_tiers.
_DEFAULT_TASK_TIERS: dict[str, Tier] = {
    "extraction": Tier.cheap,
    "reply": Tier.cheap,
    "answer": Tier.standard,
    "research": Tier.cheap,
    "planner": Tier.quality,
    "reviewer": Tier.quality,
    "judge": Tier.standard,
}


@lru_cache
def _parse_task_tiers() -> dict[str, Tier]:
    """Parse the `task=tier,task=tier` config string over the defaults."""
    settings = get_settings()
    result = dict(_DEFAULT_TASK_TIERS)
    raw = (settings.task_tiers or "").strip()
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        task, _, tier_name = pair.partition("=")
        task = task.strip().lower()
        try:
            result[task] = Tier(tier_name.strip().lower())
        except ValueError:
            continue  # ignore an unknown tier name rather than crashing startup
    return result


@lru_cache
def _tier_models() -> dict[Tier, str]:
    """Resolve each tier to a concrete model string, with fallbacks.

    quality -> standard -> cheap, and cheap ultimately falls back to the legacy
    `model` setting so a config that only sets `MODEL=...` still works.
    """
    settings = get_settings()
    cheap = settings.tier_cheap or settings.model
    standard = settings.tier_standard or settings.quality_model or cheap
    quality = settings.tier_quality or settings.quality_model or standard
    return {Tier.cheap: cheap, Tier.standard: standard, Tier.quality: quality}


def tier_for(task: str) -> Tier:
    """Return the tier assigned to a task (defaults to cheap for unknowns)."""
    return _parse_task_tiers().get(task.lower(), Tier.cheap)


def model_for(task: str) -> str:
    """Return the concrete model string a task should use."""
    settings = get_settings()
    # The judge is special-cased to preserve the existing judge_model contract
    # that the evaluation harness and the report depend on.
    if task == "judge" and settings.judge_model:
        # Only honour it if the operator hasn't explicitly tiered the judge away
        # from its default.
        if _parse_task_tiers().get("judge") == _DEFAULT_TASK_TIERS["judge"]:
            return settings.judge_model
    return _tier_models()[tier_for(task)]
