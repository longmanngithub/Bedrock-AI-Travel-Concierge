"""Progress plumbing for a crew run.

Three CrewAI mechanisms feed the checklist (verified against crewai 1.15.2):

  * `Crew(task_callback=)`  — instance-scoped, fires on task COMPLETION on both
    the sync and async paths. This is the SOURCE OF TRUTH for step state.
  * `Crew(step_callback=)`  — instance-scoped, fires on every agent step. Used
    only for cancel-checking + a coarse "still working" heartbeat.
  * `crewai_event_bus`      — a PROCESS SINGLETON. The only source of *start*
    events (what makes five research rows flip to "running" at once). Routed to
    the right job by a ContextVar, which works because `Task.execute_async` does
    `contextvars.copy_context()` before running each async task in a thread.

Non-negotiable rules for the bus (a global singleton shared by concurrent
jobs): register handlers exactly ONCE per process; never use `scoped_handlers()`
(it clears all handlers process-wide); route by `CURRENT_SINK`; and never let a
handler block or raise — progress publishing must never be able to fail a job.

The concrete Redis-backed sink lives in `app/queue/events.py`; this module only
defines the interface and the CrewAI wiring, so it stays importable without a
Redis connection (the `NoopSink` path is exactly what `evaluation/` uses).
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Callable, Protocol, runtime_checkable

logger = logging.getLogger("app.crew.progress")


class JobCancelled(Exception):
    """Raised from step_callback when a cooperative cancel has been requested.
    Propagates out of `crew.kickoff()` so the worker can mark the job cancelled."""


@runtime_checkable
class ProgressSink(Protocol):
    def step(self, key: str, state: str, detail: str = "") -> None: ...
    def activity(self, key: str, tool: str, message: str) -> None: ...
    def check_cancelled(self) -> None: ...  # raises JobCancelled if requested


class NoopSink:
    """Does nothing. The default for `evaluation/` and any direct run_crew
    caller — no Redis, no publishing, behaviour identical to the pre-queue code."""

    def step(self, key: str, state: str, detail: str = "") -> None:  # noqa: D401
        pass

    def activity(self, key: str, tool: str, message: str) -> None:
        pass

    def check_cancelled(self) -> None:
        pass


# Set before kickoff() and copied into the async research threads by CrewAI's
# `copy_context()`. The bus handlers route through CURRENT_SINK.
CURRENT_SINK: ContextVar[ProgressSink | None] = ContextVar("crew_sink", default=None)
CURRENT_JOB: ContextVar[str | None] = ContextVar("crew_job_id", default=None)

_LISTENERS_INSTALLED = False


def _task_key(obj: Any) -> str | None:
    """Best-effort extraction of a manifest step key from a CrewAI task/event."""
    for attr in ("name", "task_name"):
        val = getattr(obj, attr, None)
        if isinstance(val, str) and val:
            return val
    task = getattr(obj, "task", None)
    if task is not None:
        name = getattr(task, "name", None)
        if isinstance(name, str) and name:
            return name
    return None


def install_event_listeners() -> None:
    """Register global crewai_event_bus handlers ONCE per process.

    Wrapped in a broad try/except: the bus is a best-effort cosmetic enhancement
    (start events). If the import surface changes on a CrewAI upgrade, the
    checklist degrades to pending->done jumps driven by task_callback — ugly,
    not broken — instead of crashing the worker.
    """
    global _LISTENERS_INSTALLED
    if _LISTENERS_INSTALLED:
        return
    try:
        from crewai.events import crewai_event_bus  # type: ignore
        from crewai.events import (  # type: ignore
            TaskStartedEvent,
            ToolUsageStartedEvent,
        )
    except Exception as exc:  # noqa: BLE001 - bus is optional
        logger.warning("crewai event bus unavailable; relying on task_callback (%s)", exc)
        _LISTENERS_INSTALLED = True
        return

    @crewai_event_bus.on(TaskStartedEvent)
    def _on_task_started(source, event):  # noqa: ANN001
        sink = CURRENT_SINK.get()
        if sink is None:
            return
        key = _task_key(event) or _task_key(source)
        if not key:
            return
        try:
            sink.step(key, "running")
        except Exception:  # noqa: BLE001 - never let a handler break a run
            pass

    @crewai_event_bus.on(ToolUsageStartedEvent)
    def _on_tool_started(source, event):  # noqa: ANN001
        sink = CURRENT_SINK.get()
        if sink is None:
            return
        key = _task_key(event) or _task_key(source) or ""
        tool = getattr(event, "tool_name", "") or ""
        try:
            sink.activity(key, tool, "Searching for up-to-date details")
        except Exception:  # noqa: BLE001
            pass

    _LISTENERS_INSTALLED = True


def make_task_callback(sink: ProgressSink) -> Callable[[Any], None]:
    """task_callback: fires on task completion (sync AND async paths). Marks the
    step done. This is the contractual, instance-scoped source of truth."""

    def _cb(output: Any) -> None:
        key = _task_key(output)
        if not key:
            return
        try:
            sink.step(key, "done")
        except Exception:  # noqa: BLE001 - progress must never fail the run
            logger.debug("task_callback publish failed", exc_info=True)

    return _cb


def make_step_callback(sink: ProgressSink) -> Callable[[Any], None]:
    """step_callback: fires between agent steps — the finest-grained hook, used
    only to observe a cooperative cancel promptly."""

    def _cb(_step: Any) -> None:
        try:
            sink.check_cancelled()
        except JobCancelled:
            raise
        except Exception:  # noqa: BLE001
            pass

    return _cb
