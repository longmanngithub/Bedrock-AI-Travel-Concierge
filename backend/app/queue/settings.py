"""Redis / arq connection helpers.

Three clients, three jobs:
  * arq pool (async)   — enqueue + abort, used by the API.
  * async redis        — the SSE endpoint tails the progress stream.
  * sync redis         — the RedisProgressPublisher runs on blocking crew threads.
"""
from __future__ import annotations

import json

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from ..config import get_settings
from .keys import QUEUE_NAME


def redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(settings.queue_redis_url or settings.redis_url)


_arq_pool: ArqRedis | None = None


def serialize_job(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


async def get_arq_pool() -> ArqRedis:
    """Process-wide arq pool, created lazily (in the FastAPI lifespan)."""
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(
            redis_settings(), job_serializer=serialize_job, job_deserializer=json.loads,
            default_queue_name=QUEUE_NAME,
        )
    return _arq_pool


async def close_arq_pool() -> None:
    global _arq_pool
    if _arq_pool is not None:
        await _arq_pool.aclose()
        _arq_pool = None


def sync_redis():
    """A synchronous redis client for the progress publisher (crew threads)."""
    import redis

    return redis.Redis.from_url(get_settings().redis_url)


def async_redis():
    """An asyncio redis client for the SSE tail endpoint."""
    import redis.asyncio as aioredis

    return aioredis.from_url(get_settings().redis_url)
