"""Redis key scheme + frame constants for job progress. Leaf module (no torch)."""
from __future__ import annotations

EVENTS_MAXLEN = 500
STATE_TTL = 86_400          # 24h
CANCEL_TTL = 3_600          # 1h
FRAME_VERSION = 1
# A `running` job whose heartbeat is older than this is reaped as `expired`.
HEARTBEAT_STALE_SECONDS = 300

# Telegram account-linking (see delivery/telegram.py + queue/telegram_poll.py).
TELEGRAM_LINK_TTL = 600  # 10 minutes to tap the deep link before the code expires
TELEGRAM_OFFSET_KEY = "telegram:update_offset"


def telegram_link_key(code: str) -> str:
    return f"telegram:link:{code}"


def events_key(job_id: str) -> str:
    return f"job:{job_id}:events"


def state_key(job_id: str) -> str:
    return f"job:{job_id}:state"


def cancel_key(job_id: str) -> str:
    return f"job:{job_id}:cancel"


def arq_job_id(job_id: str) -> str:
    """arq dedupe id — makes a re-enqueue of the same job a no-op."""
    return f"crew:{job_id}"
