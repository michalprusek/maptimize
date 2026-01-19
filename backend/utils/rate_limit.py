"""Redis-based rate limiting utilities.

Provides production-ready rate limiting that works across multiple workers
and survives restarts. Uses Redis sorted sets for efficient sliding window.
"""
import logging
import time
import uuid
from typing import Optional

import redis.asyncio as redis
from fastapi import HTTPException, status

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Global Redis connection pool (lazy initialization)
_redis_pool: Optional[redis.Redis] = None


async def get_redis() -> redis.Redis:
    """Get Redis connection with lazy initialization and connection pooling."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=10,
        )
    return _redis_pool


async def check_rate_limit(
    user_id: int,
    key_prefix: str,
    max_requests: int,
    window_seconds: int,
    error_message: str = "Rate limit exceeded",
) -> None:
    """
    Check if user has exceeded rate limit using Redis sorted sets.

    Uses sliding window algorithm with atomic Redis operations.
    Fails open (allows request) if Redis is unavailable.

    Args:
        user_id: User ID to rate limit
        key_prefix: Redis key prefix (e.g., "chat", "upload")
        max_requests: Maximum requests allowed in window
        window_seconds: Time window in seconds
        error_message: Custom error message for 429 response

    Raises:
        HTTPException 429 if rate limit exceeded
    """
    try:
        r = await get_redis()
        key = f"rate_limit:{key_prefix}:{user_id}"
        now = time.time()
        window_start = now - window_seconds

        # Atomic operations: remove old entries and count current
        async with r.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            results = await pipe.execute()

        request_count = results[1]

        if request_count >= max_requests:
            # Calculate retry-after from oldest entry
            oldest_entries = await r.zrange(key, 0, 0, withscores=True)
            if oldest_entries:
                oldest_ts = oldest_entries[0][1]
                retry_after = int(oldest_ts + window_seconds - now) + 1
            else:
                retry_after = window_seconds

            logger.warning(
                f"Rate limit exceeded for user {user_id} ({key_prefix}): "
                f"{request_count} requests in {window_seconds}s"
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"{error_message}. Maximum {max_requests} requests per {_format_window(window_seconds)}.",
                headers={"Retry-After": str(retry_after)},
            )

        # Record this request with unique member to avoid collisions
        member = f"{now}:{uuid.uuid4().hex[:8]}"
        await r.zadd(key, {member: now})

        # Set TTL for auto-cleanup
        await r.expire(key, window_seconds + 60)

    except redis.RedisError as e:
        # Fail-open: allow request if Redis is unavailable
        logger.warning(f"Redis rate limit check failed ({key_prefix}), allowing request: {e}")


def _format_window(seconds: int) -> str:
    """Format window duration for user-friendly error messages."""
    if seconds >= 3600:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours > 1 else ''}"
    elif seconds >= 60:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes > 1 else ''}"
    else:
        return f"{seconds} second{'s' if seconds > 1 else ''}"
