"""RedisProgressPublisher — the concrete ProgressSink used inside crew threads.

Runs on the blocking worker threads (sync Redis client, sync DB session), so it
must be thread-safe: `max_jobs=2` means two aggregators live at once, and each
crew spawns five async research threads that all call `.step()`. A lock guards
the per-job step map.

Publishing MUST NEVER be able to fail a job — every Redis/DB call is wrapped and
swallowed. The stream (`XADD`) gives ordered replayable frames; the `state` key
holds the latest snapshot so a fresh viewer catches up in one read; the DB
heartbeat (throttled) is what the stale-job sweeper reads.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

from ..crew.manifest import initial_steps, label_for, percent_from_steps
from ..crew.progress import JobCancelled
from . import keys

logger = logging.getLogger("app.queue.events")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RedisProgressPublisher:
    def __init__(self, redis_client, job_id: str, *, session_factory=None) -> None:
        self._redis = redis_client
        self._job_id = job_id
        self._session_factory = session_factory
        self._lock = threading.Lock()
        self._steps: dict[str, dict] = {s["key"]: s for s in initial_steps()}
        self._status = "running"
        self._last_db_write = 0.0
        self._cancel_seen = False

    # -- ProgressSink interface -------------------------------------------
    def step(self, key: str, state: str, detail: str = "") -> None:
        with self._lock:
            s = self._steps.get(key)
            if s is None:
                s = {"key": key, "label": label_for(key), "state": "pending", "detail": ""}
                self._steps[key] = s
            # Never regress a done step back to running (async ordering).
            if s["state"] == "done" and state == "running":
                return
            s["state"] = state
            if detail:
                s["detail"] = detail
            percent = percent_from_steps(list(self._steps.values()))
            snapshot = self._snapshot_locked(percent)
        self._emit({
            "v": keys.FRAME_VERSION, "kind": "step", "job_id": self._job_id,
            "ts": _now_iso(), "key": key, "state": state, "percent": percent, "detail": detail,
        })
        self._store_snapshot(snapshot)
        self._maybe_heartbeat(snapshot)

    def activity(self, key: str, tool: str, message: str) -> None:
        self._emit({
            "v": keys.FRAME_VERSION, "kind": "activity", "job_id": self._job_id,
            "ts": _now_iso(), "key": key, "tool": tool, "message": message,
        })

    def check_cancelled(self) -> None:
        try:
            flag = self._redis.get(keys.cancel_key(self._job_id))
        except Exception:  # noqa: BLE001 - a redis blip must not force-cancel
            return
        if flag in (b"1", "1"):
            self._cancel_seen = True
            raise JobCancelled(self._job_id)

    # -- snapshot + terminal frames ---------------------------------------
    def _snapshot_locked(self, percent: int) -> dict:
        # Preserve manifest order.
        ordered = [self._steps[k] for k in self._steps]
        return {
            "v": keys.FRAME_VERSION, "kind": "snapshot", "job_id": self._job_id,
            "ts": _now_iso(), "status": self._status, "percent": percent,
            "steps": [dict(s) for s in ordered],
        }

    def current_snapshot(self) -> dict:
        with self._lock:
            return self._snapshot_locked(percent_from_steps(list(self._steps.values())))

    def emit_snapshot(self) -> None:
        snap = self.current_snapshot()
        self._emit(snap)
        self._store_snapshot(snap)

    def emit_terminal(self, frame: dict) -> None:
        """Emit a done/error/cancelled frame and persist it as the final state."""
        frame = {"v": keys.FRAME_VERSION, "job_id": self._job_id, "ts": _now_iso(), **frame}
        self._emit(frame)
        self._store_snapshot(frame)

    # -- low-level redis/db -----------------------------------------------
    def _emit(self, frame: dict) -> None:
        try:
            self._redis.xadd(
                keys.events_key(self._job_id),
                {"d": json.dumps(frame)},
                maxlen=keys.EVENTS_MAXLEN,
                approximate=True,
            )
            self._redis.expire(keys.events_key(self._job_id), keys.STATE_TTL)
        except Exception:  # noqa: BLE001 - progress must never fail a job
            logger.debug("xadd failed for job %s", self._job_id, exc_info=True)

    def _store_snapshot(self, snapshot: dict) -> None:
        try:
            self._redis.set(keys.state_key(self._job_id), json.dumps(snapshot), ex=keys.STATE_TTL)
        except Exception:  # noqa: BLE001
            logger.debug("state set failed for job %s", self._job_id, exc_info=True)

    def _maybe_heartbeat(self, snapshot: dict) -> None:
        """Throttled DB write of heartbeat_at + progress (at most 1 / 5s)."""
        if self._session_factory is None:
            return
        now = time.monotonic()
        if now - self._last_db_write < 5.0:
            return
        self._last_db_write = now
        try:
            from ..models import Job

            with self._session_factory() as db:
                job = db.get(Job, self._job_id)
                if job is not None:
                    job.heartbeat_at = datetime.now(timezone.utc)
                    job.progress = snapshot
                    db.commit()
        except Exception:  # noqa: BLE001
            logger.debug("heartbeat write failed for job %s", self._job_id, exc_info=True)
