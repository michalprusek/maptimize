"""Unit tests for services.job_manager.BaseJobManager (Redis mocked)."""
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest
import redis.asyncio as redis_async
from pydantic import BaseModel

from services.job_manager import BaseJobManager


class _Job(BaseModel):
    job_id: str
    user_id: int
    progress_percent: float = 0.0
    current_step: Optional[str] = None
    status: str = "pending"
    error_message: Optional[str] = None
    completed_at: Optional[datetime] = None


class _Manager(BaseJobManager[_Job]):
    _redis_key_prefix = "testjob:"
    _job_model = _Job


@pytest.fixture
def fake_redis():
    r = AsyncMock(name="redis")
    r.ping = AsyncMock()
    r.setex = AsyncMock()
    r.get = AsyncMock(return_value=None)
    return r


@pytest.fixture
def manager(fake_redis):
    m = _Manager()
    m._redis = fake_redis  # bypass connection setup
    return m


async def test_get_redis_connects_and_pings():
    m = _Manager()
    fake = AsyncMock()
    fake.ping = AsyncMock()
    with patch("services.job_manager.redis.from_url", return_value=fake) as from_url:
        r = await m._get_redis()
        assert r is fake
        from_url.assert_called_once()
        fake.ping.assert_awaited_once()
        # cached on second call
        assert await m._get_redis() is fake
        from_url.assert_called_once()


async def test_get_redis_connection_error_raises_runtime():
    m = _Manager()
    fake = AsyncMock()
    fake.ping = AsyncMock(side_effect=redis_async.ConnectionError("boom"))
    with patch("services.job_manager.redis.from_url", return_value=fake):
        with pytest.raises(RuntimeError, match="Redis connection failed"):
            await m._get_redis()


async def test_save_job_uses_setex_with_ttl(manager, fake_redis):
    job = _Job(job_id="j1", user_id=7)
    await manager._save_job(job)
    fake_redis.setex.assert_awaited_once()
    key, ttl, payload = fake_redis.setex.await_args.args
    assert key == "testjob:j1"
    assert ttl == manager._job_ttl
    assert '"job_id":"j1"' in payload


async def test_get_job_found_and_missing(manager, fake_redis):
    job = _Job(job_id="j2", user_id=1, progress_percent=50.0)
    fake_redis.get.return_value = job.model_dump_json()
    got = await manager._get_job("j2")
    assert got.job_id == "j2" and got.progress_percent == 50.0

    fake_redis.get.return_value = None
    assert await manager._get_job("missing") is None


async def test_update_progress_sets_fields(manager, fake_redis):
    job = _Job(job_id="j3", user_id=1)
    fake_redis.get.return_value = job.model_dump_json()
    await manager._update_job_progress("j3", 75.0, step="encoding", status="running")
    saved = _Job.model_validate_json(fake_redis.setex.await_args.args[2])
    assert saved.progress_percent == 75.0
    assert saved.current_step == "encoding"
    assert saved.status == "running"


async def test_update_progress_noop_when_missing(manager, fake_redis):
    fake_redis.get.return_value = None
    await manager._update_job_progress("nope", 10.0)
    fake_redis.setex.assert_not_awaited()


async def test_mark_completed_and_error(manager, fake_redis):
    job = _Job(job_id="j4", user_id=1)
    fake_redis.get.return_value = job.model_dump_json()
    done = await manager._mark_job_completed("j4")
    assert done.status == "completed" and done.completed_at is not None

    fake_redis.get.return_value = job.model_dump_json()
    failed = await manager._mark_job_error("j4", "bad thing")
    assert failed.status == "error" and failed.error_message == "bad thing"


async def test_update_status_missing_returns_none(manager, fake_redis):
    fake_redis.get.return_value = None
    assert await manager._update_job_status("x", status="completed") is None


async def test_get_job_for_user_ownership(manager, fake_redis):
    job = _Job(job_id="j5", user_id=42)
    fake_redis.get.return_value = job.model_dump_json()
    assert (await manager.get_job_for_user("j5", 42)).job_id == "j5"
    fake_redis.get.return_value = job.model_dump_json()
    assert await manager.get_job_for_user("j5", 999) is None
