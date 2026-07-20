"""arq worker entrypoint.

Run with:  arq app.queue.worker.WorkerSettings

ONE worker process with `max_jobs=2`: two kickoffs share a single torch/Chroma/
embedding-model load (~1.2 GB); a second *process* would double that resident
cost for zero throughput gain, since the bottleneck is LLM latency, not CPU.

Startup warms the embedding model once (the documented SIGABRT is a race on the
first concurrent load — see crew/tools.py) and registers the event-bus listeners
exactly once, before any job runs.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from arq import cron

from ..config import get_settings
from .delivery import deliver_itinerary_job
from .jobs import reap_stale_jobs, run_itinerary_job
from .notifications import send_response_email_job
from .settings import redis_settings, sync_redis
from .telegram_poll import poll_telegram_updates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.queue.worker")


async def startup(ctx: dict) -> None:
    settings = get_settings()
    # 1) Warm the embedding model ONCE per process, before accepting jobs.
    if settings.rag_enabled:
        try:
            from ..crew.tools import warm_knowledge_base

            warm_knowledge_base()
        except Exception:  # noqa: BLE001 - degrade rather than refuse to start
            logger.warning("knowledge base warmup failed", exc_info=True)
    # 2) Register global crewai event-bus listeners ONCE.
    from ..crew.progress import install_event_listeners

    install_event_listeners()
    # 3) Bounded executor: at most max_jobs blocking kickoffs at once.
    ctx["executor"] = ThreadPoolExecutor(
        max_workers=settings.worker_max_jobs, thread_name_prefix="crew"
    )
    # 4) Sync redis client for the progress publisher (used on crew threads).
    ctx["redis_raw"] = sync_redis()
    logger.info("worker started (max_jobs=%s)", settings.worker_max_jobs)


async def shutdown(ctx: dict) -> None:
    executor: ThreadPoolExecutor | None = ctx.get("executor")
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)
    redis_raw = ctx.get("redis_raw")
    if redis_raw is not None:
        try:
            redis_raw.close()
        except Exception:  # noqa: BLE001
            pass


class WorkerSettings:
    functions = [run_itinerary_job, deliver_itinerary_job, send_response_email_job]
    cron_jobs = [
        cron(reap_stale_jobs, second=0),
        # Telegram linking latency: how long a user waits after tapping the
        # deep link before the bot confirms. is_configured() gates this to a
        # no-op tick when TELEGRAM_BOT_TOKEN is unset.
        cron(poll_telegram_updates, second={0, 15, 30, 45}),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    max_jobs = get_settings().worker_max_jobs
    job_timeout = int(get_settings().job_timeout)
    max_tries = 2
    keep_result = 0            # Postgres is the store of record
    health_check_interval = 30
