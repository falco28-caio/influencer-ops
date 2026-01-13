from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import redis.asyncio as redis

from src.core.config import settings

redis_client: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """Get the Redis client instance."""
    global redis_client
    if redis_client is None:
        redis_client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return redis_client


async def close_redis() -> None:
    """Close Redis connection."""
    global redis_client
    if redis_client is not None:
        await redis_client.close()
        redis_client = None


@asynccontextmanager
async def redis_context() -> AsyncGenerator[redis.Redis, None]:
    """Context manager for Redis operations."""
    client = await get_redis()
    try:
        yield client
    finally:
        pass  # Don't close the shared client


async def check_kill_switch() -> bool:
    """Check if the global kill switch is enabled."""
    client = await get_redis()
    value = await client.get(settings.kill_switch_key)
    return value == "true" or value == "1"


async def set_kill_switch(enabled: bool) -> None:
    """Set the global kill switch."""
    client = await get_redis()
    if enabled:
        await client.set(settings.kill_switch_key, "true")
    else:
        await client.delete(settings.kill_switch_key)
