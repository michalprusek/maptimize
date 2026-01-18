"""
Shared Redis job manager for export/import services.

Provides a DRY base class for managing job state in Redis,
eliminating duplicate code between ExportService and ImportService.
"""
import logging
from datetime import datetime, timezone
from typing import Generic, Optional, TypeVar

import redis.asyncio as redis
from pydantic import BaseModel

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Default job TTL: 24 hours
DEFAULT_JOB_TTL = 3600 * 24

T = TypeVar("T", bound=BaseModel)


class BaseJobManager(Generic[T]):
    """
    Base class for Redis-backed job state management.

    Provides common functionality for saving, retrieving, and updating
    job state in Redis. Subclasses should specify the job data model
    and Redis key prefix.
    """

    _redis_key_prefix: str = "job:"
    _job_ttl: int = DEFAULT_JOB_TTL
    _job_model: type[T]

    def __init__(self) -> None:
        self._redis: Optional[redis.Redis] = None

    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis connection."""
        if self._redis is None:
            try:
                self._redis = redis.from_url(settings.redis_url)
                # Test connection
                await self._redis.ping()
            except redis.ConnectionError as e:
                logger.error(f"Failed to connect to Redis at {settings.redis_url}: {e}")
                raise RuntimeError(f"Redis connection failed: {e}") from e
        return self._redis

    async def _save_job(self, job: T) -> None:
        """Save job data to Redis."""
        r = await self._get_redis()
        key = f"{self._redis_key_prefix}{job.job_id}"
        await r.setex(key, self._job_ttl, job.model_dump_json())

    async def _get_job(self, job_id: str) -> Optional[T]:
        """Get job data from Redis."""
        r = await self._get_redis()
        key = f"{self._redis_key_prefix}{job_id}"
        data = await r.get(key)
        if data:
            return self._job_model.model_validate_json(data)
        return None

    async def _update_job_progress(
        self,
        job_id: str,
        progress: float,
        step: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None:
        """Update job progress in Redis."""
        job = await self._get_job(job_id)
        if job:
            job.progress_percent = progress
            if step:
                job.current_step = step
            if status:
                job.status = status
            await self._save_job(job)

    async def _mark_job_completed(self, job_id: str) -> Optional[T]:
        """Mark job as completed with timestamp."""
        return await self._update_job_status(
            job_id,
            status="completed",
            completed_at=datetime.now(timezone.utc),
        )

    async def _mark_job_error(self, job_id: str, error_message: str) -> Optional[T]:
        """Mark job as failed with error message."""
        return await self._update_job_status(
            job_id,
            status="error",
            error_message=error_message,
        )

    async def _update_job_status(self, job_id: str, **updates) -> Optional[T]:
        """Update job with arbitrary fields and save."""
        job = await self._get_job(job_id)
        if not job:
            return None
        for key, value in updates.items():
            setattr(job, key, value)
        await self._save_job(job)
        return job

    async def get_job_for_user(self, job_id: str, user_id: int) -> Optional[T]:
        """
        Get job data if it belongs to the specified user.

        Returns job data if found and owned by user, None otherwise.
        """
        job = await self._get_job(job_id)
        if job and job.user_id == user_id:
            return job
        return None
