"""Shared slowapi Limiter instance.

A leaf module (no app-internal imports besides `config`) so both `main.py` and
route modules under `routers/` can decorate endpoints with
`@limiter.limit(...)` without a circular import — `main.py` imports router
modules at module load time, before it would otherwise define this instance
itself.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from .config import get_settings

# Redis-backed (not the slowapi default of in-memory) so limits are shared
# across uvicorn worker processes (see WEB_CONCURRENCY) and survive a
# redeploy, using the same Redis instance the arq queue already depends on.
limiter = Limiter(key_func=get_remote_address, storage_uri=get_settings().redis_url)
