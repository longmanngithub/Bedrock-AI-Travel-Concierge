"""The crew's step manifest — a LEAF module that imports nothing.

This is deliberately dependency-free so the API process (routers/jobs.py, the
frontend-facing layer) can read the checklist labels/weights WITHOUT importing
crewai -> litellm -> sentence-transformers -> torch -> chromadb. Keeping torch
out of the API process is what makes the ~900 MB memory saving real; a stray
`from .crew.tasks import ...` in a router would silently undo it.

Labels live here (server-side) so a later phase can add/remove agents without a
frontend deploy — the UI renders whatever `steps` the server sends.
"""
from __future__ import annotations

STEP_MANIFEST: list[dict] = [
    {"key": "destination", "label": "Researching destination", "weight": 1},
    {"key": "food", "label": "Finding places to eat", "weight": 1},
    {"key": "personalization", "label": "Matching your interests", "weight": 1},
    {"key": "accommodation", "label": "Comparing places to stay", "weight": 1},
    {"key": "budget", "label": "Estimating costs", "weight": 1},
    {"key": "planner", "label": "Building the day-by-day", "weight": 2},
    {"key": "reviewer", "label": "Reviewing and finalising", "weight": 2},
]

STEP_KEYS: list[str] = [s["key"] for s in STEP_MANIFEST]
_LABELS: dict[str, str] = {s["key"]: s["label"] for s in STEP_MANIFEST}
_WEIGHTS: dict[str, int] = {s["key"]: s["weight"] for s in STEP_MANIFEST}
_TOTAL_WEIGHT: int = sum(_WEIGHTS.values())


def label_for(key: str) -> str:
    return _LABELS.get(key, key)


def initial_steps() -> list[dict]:
    """A fresh, all-pending checklist."""
    return [
        {"key": s["key"], "label": s["label"], "state": "pending", "detail": ""}
        for s in STEP_MANIFEST
    ]


def percent_from_steps(steps: list[dict]) -> int:
    """Weighted completion. Monotonic and correct under concurrency: the five
    research steps finish in arbitrary order, so we never emit 'step N of 7' —
    only done_weight / total_weight."""
    done = sum(_WEIGHTS.get(s["key"], 0) for s in steps if s.get("state") == "done")
    if not _TOTAL_WEIGHT:
        return 0
    return int(round(100 * done / _TOTAL_WEIGHT))
