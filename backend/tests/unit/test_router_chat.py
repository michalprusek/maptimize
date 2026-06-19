"""In-process unit tests for ``routers.chat``.

The chat router exposes thread/message CRUD plus async AI generation. We call the
route handlers directly with the ``mock_db`` AsyncMock fixture and fake users
(``SimpleNamespace``). The AI generation service, Redis client and
``async_session_maker`` are patched at the router boundary so no real Gemini
call, Redis connection or DB engine is touched.

Pydantic ``model_validate`` runs with ``from_attributes=True`` against the ORM
objects, so the fake thread/message namespaces carry every field the response
schemas read.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.asyncio as redis_async
from fastapi import HTTPException

import routers.chat as r
from tests.unit.conftest import make_result


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def fake_user(uid=1):
    return SimpleNamespace(id=uid, email="a@b.cz",
                           role=SimpleNamespace(value="researcher"))


def make_thread(thread_id=1, user_id=1, name="New Chat",
                generation_status="idle", task_id=None,
                started_at=None, error=None, messages=None):
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=thread_id,
        user_id=user_id,
        name=name,
        created_at=now,
        updated_at=now,
        generation_status=generation_status,
        generation_task_id=task_id,
        generation_started_at=started_at,
        generation_error=error,
        messages=messages or [],
    )


def make_message(msg_id=1, thread_id=1, role="user", content="hi",
                 interaction_id=None):
    return SimpleNamespace(
        id=msg_id,
        thread_id=thread_id,
        role=role,
        content=content,
        citations=[],
        image_refs=[],
        tool_calls=[],
        interaction_id=interaction_id,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def fake_redis(**kw):
    redis = AsyncMock(name="redis")
    redis.get = AsyncMock(return_value=kw.get("get", None))
    redis.setex = AsyncMock()
    redis.delete = AsyncMock()
    redis.zadd = AsyncMock()
    redis.expire = AsyncMock()
    redis.zrange = AsyncMock(return_value=kw.get("zrange", []))
    return redis


def fake_pipeline(zcard_count=0):
    """A redis pipeline context manager returning [zremrangebyscore, zcard]."""
    pipe = AsyncMock()
    pipe.zremrangebyscore = MagicMock()
    pipe.zcard = MagicMock()
    pipe.execute = AsyncMock(return_value=[0, zcard_count])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=pipe)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# =========================================================================== #
# _check_rate_limit_async
# =========================================================================== #
async def test_rate_limit_under_limit_records_request():
    redis = fake_redis()
    redis.pipeline = MagicMock(return_value=fake_pipeline(zcard_count=3))
    with patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)):
        await r._check_rate_limit_async(1)
    redis.zadd.assert_awaited_once()
    redis.expire.assert_awaited_once()


async def test_rate_limit_exceeded_with_oldest_entry():
    redis = fake_redis(zrange=[("m", 1000.0)])
    redis.pipeline = MagicMock(
        return_value=fake_pipeline(zcard_count=r.AI_RATE_LIMIT_REQUESTS)
    )
    with patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)), \
         patch.object(r.time, "time", return_value=1000.0):
        with pytest.raises(HTTPException) as exc:
            await r._check_rate_limit_async(1)
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


async def test_rate_limit_exceeded_no_oldest_entry():
    # zcard at limit but zrange empty -> retry_after defaults to window.
    redis = fake_redis(zrange=[])
    redis.pipeline = MagicMock(
        return_value=fake_pipeline(zcard_count=r.AI_RATE_LIMIT_REQUESTS + 5)
    )
    with patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)):
        with pytest.raises(HTTPException) as exc:
            await r._check_rate_limit_async(1)
    assert exc.value.headers["Retry-After"] == str(r.AI_RATE_LIMIT_WINDOW)


async def test_rate_limit_redis_error_fails_open():
    # A RedisError anywhere in the check is swallowed (fail-open).
    redis = fake_redis()
    redis.pipeline = MagicMock(side_effect=redis_async.RedisError("down"))
    with patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)):
        # No exception raised -> request allowed.
        await r._check_rate_limit_async(1)


# =========================================================================== #
# _get_redis (lazy pool)
# =========================================================================== #
async def test_get_redis_lazy_init_and_cached():
    r._redis_pool = None
    fake = MagicMock(name="pool")
    with patch.object(r.redis, "from_url", return_value=fake) as from_url:
        got = await r._get_redis()
        assert got is fake
        # second call uses cached pool
        assert await r._get_redis() is fake
        from_url.assert_called_once()
    r._redis_pool = None


# =========================================================================== #
# _check_rate_limit (sync wrapper)
# =========================================================================== #
def test_check_rate_limit_sync_loop_not_running():
    # A non-running loop exists -> loop.run_until_complete branch (line 136).
    loop = MagicMock()
    loop.is_running.return_value = False
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()), \
         patch.object(r.asyncio, "get_event_loop", return_value=loop):
        r._check_rate_limit(5)
    loop.run_until_complete.assert_called_once()


def test_check_rate_limit_sync_no_event_loop():
    # get_event_loop raises RuntimeError -> asyncio.run fallback (line 138-139).
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()), \
         patch.object(r.asyncio, "get_event_loop",
                      side_effect=RuntimeError("no loop")), \
         patch.object(r.asyncio, "run") as run:
        r._check_rate_limit(5)
    run.assert_called_once()


async def test_check_rate_limit_sync_within_running_loop():
    # Inside a running loop -> create_task branch (line 134).
    created = []

    def fake_create_task(coro):
        created.append(coro)
        coro.close()
        return MagicMock()

    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()), \
         patch.object(r.asyncio, "create_task", side_effect=fake_create_task):
        r._check_rate_limit(9)
    assert len(created) == 1


# =========================================================================== #
# get_thread_for_user
# =========================================================================== #
async def test_get_thread_for_user_found(mock_db):
    thread = make_thread()
    mock_db.execute.return_value = make_result(scalar=thread)
    got = await r.get_thread_for_user(mock_db, 1, 1)
    assert got is thread


async def test_get_thread_for_user_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await r.get_thread_for_user(mock_db, 1, 1)
    assert exc.value.status_code == 404


# =========================================================================== #
# _create_assistant_message
# =========================================================================== #
def test_create_assistant_message_defaults():
    msg = r._create_assistant_message(7, {"content": "hello"})
    assert msg.thread_id == 7
    assert msg.role == "assistant"
    assert msg.content == "hello"
    assert msg.citations == [] and msg.image_refs == [] and msg.tool_calls == []


def test_create_assistant_message_override():
    msg = r._create_assistant_message(
        7, {"content": "orig", "interaction_id": "i1"}, content_override="WARNED")
    assert msg.content == "WARNED"
    assert msg.interaction_id == "i1"


# =========================================================================== #
# _run_generation_task  — background generation
# =========================================================================== #
def _session_cm(db):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def test_run_generation_user_deleted(mock_db):
    mock_db.get = AsyncMock(return_value=None)  # user missing
    with patch.object(r, "async_session_maker", return_value=_session_cm(mock_db)), \
         patch.object(r, "_get_redis", new=AsyncMock(return_value=fake_redis())):
        await r._run_generation_task("t1", 1, 99, "q")
    # No thread fetched / commit beyond the early return.
    mock_db.commit.assert_not_awaited()


async def test_run_generation_thread_missing(mock_db):
    user = SimpleNamespace(id=1)
    mock_db.get = AsyncMock(side_effect=[user, None])  # user ok, thread None
    with patch.object(r, "async_session_maker", return_value=_session_cm(mock_db)), \
         patch.object(r, "_get_redis", new=AsyncMock(return_value=fake_redis())):
        await r._run_generation_task("t1", 1, 1, "q")
    mock_db.commit.assert_not_awaited()


async def test_run_generation_ownership_changed(mock_db):
    user = SimpleNamespace(id=1)
    thread = make_thread(user_id=2)  # owned by someone else
    mock_db.get = AsyncMock(side_effect=[user, thread])
    with patch.object(r, "async_session_maker", return_value=_session_cm(mock_db)), \
         patch.object(r, "_get_redis", new=AsyncMock(return_value=fake_redis())):
        await r._run_generation_task("t1", 1, 1, "q")
    mock_db.commit.assert_not_awaited()


async def test_run_generation_cancelled_before_start(mock_db):
    user = SimpleNamespace(id=1)
    thread = make_thread(user_id=1)
    mock_db.get = AsyncMock(side_effect=[user, thread])
    redis = fake_redis(get="1")  # cancel flag set
    with patch.object(r, "async_session_maker", return_value=_session_cm(mock_db)), \
         patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)):
        await r._run_generation_task("t1", 1, 1, "q")
    assert thread.generation_status == "cancelled"
    assert thread.generation_task_id is None


async def test_run_generation_success(mock_db):
    user = SimpleNamespace(id=1)
    thread = make_thread(user_id=1)
    mock_db.get = AsyncMock(side_effect=[user, thread])
    redis = fake_redis(get=None)  # no cancel flag
    gen = AsyncMock(return_value={"content": "answer", "interaction_id": "ix"})
    with patch.object(r, "async_session_maker", return_value=_session_cm(mock_db)), \
         patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)), \
         patch("services.gemini_agent_service.generate_response", new=gen):
        await r._run_generation_task("t1", 1, 1, "q")
    assert thread.generation_status == "completed"
    mock_db.add.assert_called_once()
    redis.delete.assert_awaited()  # finally cleanup


async def test_run_generation_success_with_context_warning(mock_db):
    user = SimpleNamespace(id=1)
    thread = make_thread(user_id=1)
    mock_db.get = AsyncMock(side_effect=[user, thread])
    redis = fake_redis(get=None)
    gen = AsyncMock(return_value={"content": "answer"})
    added = {}
    mock_db.add = MagicMock(side_effect=lambda m: added.update(content=m.content))
    with patch.object(r, "async_session_maker", return_value=_session_cm(mock_db)), \
         patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)), \
         patch("services.gemini_agent_service.generate_response", new=gen):
        await r._run_generation_task("t1", 1, 1, "q",
                                     context_warning="Long conversation.")
    assert added["content"].startswith("⚠️")
    assert "answer" in added["content"]
    assert thread.generation_status == "completed"


async def test_run_generation_cancelled_during_generation(mock_db):
    user = SimpleNamespace(id=1)
    thread = make_thread(user_id=1)
    mock_db.get = AsyncMock(side_effect=[user, thread])
    # First get (before start) -> None; second get (during) -> "1".
    redis = fake_redis()
    redis.get = AsyncMock(side_effect=[None, "1"])
    gen = AsyncMock(return_value={"content": "answer"})
    with patch.object(r, "async_session_maker", return_value=_session_cm(mock_db)), \
         patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)), \
         patch("services.gemini_agent_service.generate_response", new=gen):
        await r._run_generation_task("t1", 1, 1, "q")
    assert thread.generation_status == "cancelled"
    mock_db.add.assert_not_called()


async def test_run_generation_cancelled_error(mock_db):
    user = SimpleNamespace(id=1)
    thread = make_thread(user_id=1)
    mock_db.get = AsyncMock(side_effect=[user, thread])
    redis = fake_redis(get=None)
    gen = AsyncMock(side_effect=asyncio.CancelledError())
    with patch.object(r, "async_session_maker", return_value=_session_cm(mock_db)), \
         patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)), \
         patch("services.gemini_agent_service.generate_response", new=gen):
        await r._run_generation_task("t1", 1, 1, "q")
    assert thread.generation_status == "cancelled"


async def test_run_generation_generation_exception(mock_db):
    user = SimpleNamespace(id=1)
    thread = make_thread(user_id=1)
    mock_db.get = AsyncMock(side_effect=[user, thread])
    redis = fake_redis(get=None)
    gen = AsyncMock(side_effect=RuntimeError("gemini blew up"))
    with patch.object(r, "async_session_maker", return_value=_session_cm(mock_db)), \
         patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)), \
         patch("services.gemini_agent_service.generate_response", new=gen):
        await r._run_generation_task("t1", 1, 1, "q")
    assert thread.generation_status == "error"
    assert "gemini blew up" in thread.generation_error


async def test_run_generation_fatal_error_updates_thread(mock_db):
    # The outer try fails (session maker raises). The fatal-error handler opens a
    # fresh session and updates a "generating" thread to error.
    error_thread = make_thread(generation_status="generating")
    error_db = AsyncMock(name="error_db")
    error_db.get = AsyncMock(return_value=error_thread)
    error_db.commit = AsyncMock()

    call = {"n": 0}

    def maker():
        call["n"] += 1
        if call["n"] == 1:
            raise RuntimeError("session boom")
        return _session_cm(error_db)

    with patch.object(r, "async_session_maker", side_effect=maker), \
         patch.object(r, "_get_redis", new=AsyncMock(return_value=fake_redis())):
        await r._run_generation_task("t1", 1, 1, "q")
    assert error_thread.generation_status == "error"
    assert "initialization failed" in error_thread.generation_error


async def test_run_generation_fatal_error_recovery_also_fails(mock_db):
    # Outer try fails AND the recovery session also fails -> inner except logs.
    def maker():
        raise RuntimeError("everything down")

    with patch.object(r, "async_session_maker", side_effect=maker), \
         patch.object(r, "_get_redis", new=AsyncMock(return_value=fake_redis())):
        # Should not raise.
        await r._run_generation_task("t1", 1, 1, "q")


async def test_run_generation_finally_redis_error(mock_db):
    # finally block: redis delete raises RedisError -> warning branch.
    user = SimpleNamespace(id=1)
    thread = make_thread(user_id=1)
    mock_db.get = AsyncMock(side_effect=[user, thread])
    redis = fake_redis(get=None)
    redis.delete = AsyncMock(side_effect=redis_async.RedisError("cleanup fail"))
    gen = AsyncMock(return_value={"content": "answer"})
    with patch.object(r, "async_session_maker", return_value=_session_cm(mock_db)), \
         patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)), \
         patch("services.gemini_agent_service.generate_response", new=gen):
        await r._run_generation_task("t1", 1, 1, "q")
    assert thread.generation_status == "completed"


async def test_run_generation_finally_redis_generic_error(mock_db):
    user = SimpleNamespace(id=1)
    thread = make_thread(user_id=1)
    mock_db.get = AsyncMock(side_effect=[user, thread])
    redis = fake_redis(get=None)
    gen = AsyncMock(return_value={"content": "answer"})
    # _get_redis raises in the finally cleanup only: first call (body) succeeds,
    # the second call (finally cleanup) raises a non-RedisError -> lines 325-326.
    redis_calls = {"n": 0}

    async def get_redis():
        redis_calls["n"] += 1
        if redis_calls["n"] >= 2:  # the finally call
            raise RuntimeError("redis gone")
        return redis

    with patch.object(r, "async_session_maker", return_value=_session_cm(mock_db)), \
         patch.object(r, "_get_redis", new=get_redis), \
         patch("services.gemini_agent_service.generate_response", new=gen):
        await r._run_generation_task("t1", 1, 1, "q")
    assert thread.generation_status == "completed"


# =========================================================================== #
# _cancel_generation
# =========================================================================== #
async def test_cancel_generation_active_task():
    redis = fake_redis()
    task = MagicMock()
    task.done.return_value = False
    r._active_tasks["tk"] = task
    try:
        with patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)):
            result = await r._cancel_generation("tk")
        assert result is True
        task.cancel.assert_called_once()
    finally:
        r._active_tasks.pop("tk", None)


async def test_cancel_generation_no_active_task():
    redis = fake_redis()
    with patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)):
        result = await r._cancel_generation("missing")
    assert result is False


async def test_cancel_generation_task_already_done():
    redis = fake_redis()
    task = MagicMock()
    task.done.return_value = True
    r._active_tasks["dn"] = task
    try:
        with patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)):
            result = await r._cancel_generation("dn")
        assert result is False
        task.cancel.assert_not_called()
    finally:
        r._active_tasks.pop("dn", None)


async def test_cancel_generation_redis_error():
    redis = fake_redis()
    redis.setex = AsyncMock(side_effect=redis_async.RedisError("down"))
    with patch.object(r, "_get_redis", new=AsyncMock(return_value=redis)):
        with pytest.raises(HTTPException) as exc:
            await r._cancel_generation("tk")
    assert exc.value.status_code == 500


# =========================================================================== #
# list_threads
# =========================================================================== #
async def test_list_threads(mock_db):
    thread = make_thread()
    result = make_result()
    result.unique.return_value.all.return_value = [(thread, 4)]
    mock_db.execute.return_value = result
    out = await r.list_threads(skip=0, limit=50,
                               current_user=fake_user(), db=mock_db)
    assert len(out) == 1
    assert out[0].message_count == 4


async def test_list_threads_null_count(mock_db):
    thread = make_thread()
    result = make_result()
    result.unique.return_value.all.return_value = [(thread, None)]
    mock_db.execute.return_value = result
    out = await r.list_threads(skip=0, limit=50,
                               current_user=fake_user(), db=mock_db)
    assert out[0].message_count == 0


# =========================================================================== #
# create_thread
# =========================================================================== #
async def test_create_thread_default_name(mock_db):
    async def fake_flush():
        # mimic db.flush populating server defaults
        pass
    mock_db.flush = AsyncMock(side_effect=fake_flush)
    added = {}
    mock_db.add = MagicMock(side_effect=lambda t: added.update(t=t))

    async def flush2():
        added["t"].id = 12
        added["t"].created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        added["t"].updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    mock_db.flush.side_effect = flush2
    out = await r.create_thread(data=None, current_user=fake_user(), db=mock_db)
    assert out.name == "New Chat"
    assert added["t"].user_id == 1


async def test_create_thread_with_name(mock_db):
    added = {}

    async def flush():
        added["t"].id = 13
        added["t"].created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        added["t"].updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    mock_db.add = MagicMock(side_effect=lambda t: added.update(t=t))
    mock_db.flush = AsyncMock(side_effect=flush)
    out = await r.create_thread(
        data=r.ChatThreadCreate(name="My Chat"),
        current_user=fake_user(), db=mock_db)
    assert out.name == "My Chat"


# =========================================================================== #
# get_thread
# =========================================================================== #
async def test_get_thread_with_messages(mock_db):
    msgs = [make_message(1, role="user"), make_message(2, role="assistant")]
    thread = make_thread(messages=msgs)
    mock_db.execute.return_value = make_result(scalar=thread)
    out = await r.get_thread(thread_id=1, current_user=fake_user(), db=mock_db)
    assert out.message_count == 2
    assert len(out.messages) == 2


async def test_get_thread_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await r.get_thread(thread_id=1, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 404


# =========================================================================== #
# update_thread
# =========================================================================== #
async def test_update_thread(mock_db):
    thread = make_thread()
    mock_db.execute.return_value = make_result(scalar=thread)
    out = await r.update_thread(
        thread_id=1, data=r.ChatThreadUpdate(name="Renamed"),
        current_user=fake_user(), db=mock_db)
    assert thread.name == "Renamed"
    assert out.name == "Renamed"
    mock_db.commit.assert_awaited()
    mock_db.refresh.assert_awaited()


# =========================================================================== #
# delete_thread
# =========================================================================== #
async def test_delete_thread(mock_db):
    thread = make_thread()
    mock_db.execute.return_value = make_result(scalar=thread)
    await r.delete_thread(thread_id=1, current_user=fake_user(), db=mock_db)
    mock_db.delete.assert_awaited_once_with(thread)
    mock_db.commit.assert_awaited()


# =========================================================================== #
# send_message
# =========================================================================== #
async def test_send_message_thread_not_found(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await r.send_message(
                thread_id=1, data=r.ChatMessageCreate(content="hi"),
                current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 404


async def test_send_message_already_generating(mock_db):
    thread = make_thread(generation_status="generating")
    mock_db.execute.return_value = make_result(scalar=thread)
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await r.send_message(
                thread_id=1, data=r.ChatMessageCreate(content="hi"),
                current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 409


async def test_send_message_success_creates_task(mock_db):
    thread = make_thread(name="New Chat")
    last_assistant = make_message(5, role="assistant", interaction_id="prev-ix")
    user_msg = make_message(6, role="user", content="Tell me about cells")
    mock_db.execute.side_effect = [
        make_result(scalar=thread),          # thread lookup (with_for_update)
        make_result(scalar=last_assistant),  # last assistant interaction_id
        make_result(scalar=2),               # message count
    ]

    async def refresh(m):
        m.id = 6
        m.citations = []
        m.image_refs = []
        m.tool_calls = []
        m.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    mock_db.refresh = AsyncMock(side_effect=refresh)

    captured = {}
    fake_task = MagicMock()

    def fake_create_task(coro):
        captured["coro"] = coro
        coro.close()  # don't actually run the background task
        return fake_task

    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()), \
         patch.object(r.asyncio, "create_task", side_effect=fake_create_task):
        out = await r.send_message(
            thread_id=1, data=r.ChatMessageCreate(content="Tell me about cells"),
            current_user=fake_user(), db=mock_db)
    assert out.generation_status == "generating"
    assert out.task_id.startswith("gen_1_")
    # Default-named thread renamed from first message.
    assert thread.name == "Tell me about cells"
    assert thread.generation_status == "generating"
    # task tracked
    assert out.task_id in r._active_tasks

    # Invoke the done-callback registered by send_message (lines 572-578).
    done_callback = fake_task.add_done_callback.call_args.args[0]
    fake_task.cancelled.return_value = True
    done_callback(fake_task)
    assert out.task_id not in r._active_tasks  # callback cleaned it up

    # Re-register and exercise the exception branch (577-578).
    r._active_tasks[out.task_id] = fake_task
    fake_task.cancelled.return_value = False
    fake_task.exception.return_value = RuntimeError("boom")
    done_callback(fake_task)
    assert out.task_id not in r._active_tasks
    r._active_tasks.pop(out.task_id, None)


async def test_send_message_long_conversation_warning(mock_db):
    thread = make_thread(name="Existing name")
    long_content = "x" * 60  # > 50 chars, but name not default so not renamed
    mock_db.execute.side_effect = [
        make_result(scalar=thread),
        make_result(scalar=None),   # no previous assistant
        make_result(scalar=75),     # > 50 messages -> context warning
    ]

    async def refresh(m):
        m.id = 7
        m.citations = []
        m.image_refs = []
        m.tool_calls = []
        m.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    mock_db.refresh = AsyncMock(side_effect=refresh)

    captured = {}

    def fake_create_task(coro):
        # Inspect coroutine args via the closure isn't trivial; just close it.
        captured["created"] = True
        coro.close()
        return MagicMock()

    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()), \
         patch.object(r.asyncio, "create_task", side_effect=fake_create_task):
        out = await r.send_message(
            thread_id=1, data=r.ChatMessageCreate(content=long_content),
            current_user=fake_user(), db=mock_db)
    assert captured["created"] is True
    assert thread.name == "Existing name"  # unchanged
    r._active_tasks.pop(out.task_id, None)


# =========================================================================== #
# get_generation_status
# =========================================================================== #
async def test_generation_status_idle(mock_db):
    thread = make_thread(generation_status="idle")
    mock_db.execute.return_value = make_result(scalar=thread)
    out = await r.get_generation_status(
        thread_id=1, current_user=fake_user(), db=mock_db)
    assert out.status == "idle"
    assert out.elapsed_seconds is None


async def test_generation_status_generating_elapsed(mock_db):
    started = datetime.now(timezone.utc) - timedelta(seconds=30)
    thread = make_thread(generation_status="generating",
                         task_id="tk", started_at=started)
    mock_db.execute.return_value = make_result(scalar=thread)
    out = await r.get_generation_status(
        thread_id=1, current_user=fake_user(), db=mock_db)
    assert out.status == "generating"
    assert out.elapsed_seconds is not None and out.elapsed_seconds >= 29


async def test_generation_status_completed_with_message(mock_db):
    thread = make_thread(generation_status="completed")
    latest = make_message(9, role="assistant", content="done")
    mock_db.execute.side_effect = [
        make_result(scalar=thread),    # get_thread_for_user
        make_result(scalar=latest),    # latest assistant message
    ]
    out = await r.get_generation_status(
        thread_id=1, current_user=fake_user(), db=mock_db)
    assert out.status == "completed"
    assert out.message is not None
    assert out.message.content == "done"
    # status reset to idle and committed
    assert thread.generation_status == "idle"
    mock_db.commit.assert_awaited()


async def test_generation_status_completed_no_message(mock_db):
    thread = make_thread(generation_status="completed")
    mock_db.execute.side_effect = [
        make_result(scalar=thread),
        make_result(scalar=None),   # no assistant message found
    ]
    out = await r.get_generation_status(
        thread_id=1, current_user=fake_user(), db=mock_db)
    assert out.message is None
    assert thread.generation_status == "idle"


async def test_generation_status_none_defaults_idle(mock_db):
    thread = make_thread(generation_status=None)
    mock_db.execute.return_value = make_result(scalar=thread)
    out = await r.get_generation_status(
        thread_id=1, current_user=fake_user(), db=mock_db)
    assert out.status == "idle"


# =========================================================================== #
# cancel_generation
# =========================================================================== #
async def test_cancel_generation_endpoint_not_generating(mock_db):
    thread = make_thread(generation_status="idle")
    mock_db.execute.return_value = make_result(scalar=thread)
    with pytest.raises(HTTPException) as exc:
        await r.cancel_generation(thread_id=1, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 400


async def test_cancel_generation_endpoint_with_task(mock_db):
    thread = make_thread(generation_status="generating", task_id="tk-77")
    mock_db.execute.return_value = make_result(scalar=thread)
    with patch.object(r, "_cancel_generation", new=AsyncMock()) as cancel:
        out = await r.cancel_generation(
            thread_id=1, current_user=fake_user(), db=mock_db)
    cancel.assert_awaited_once_with("tk-77")
    assert out["status"] == "cancelled"
    assert thread.generation_status == "cancelled"
    assert thread.generation_task_id is None


async def test_cancel_generation_endpoint_no_task_id(mock_db):
    thread = make_thread(generation_status="generating", task_id=None)
    mock_db.execute.return_value = make_result(scalar=thread)
    with patch.object(r, "_cancel_generation", new=AsyncMock()) as cancel:
        out = await r.cancel_generation(
            thread_id=1, current_user=fake_user(), db=mock_db)
    cancel.assert_not_awaited()
    assert out["status"] == "cancelled"


# =========================================================================== #
# list_messages
# =========================================================================== #
async def test_list_messages(mock_db):
    thread = make_thread()
    msgs = [make_message(1), make_message(2)]
    mock_db.execute.side_effect = [
        make_result(scalar=thread),         # get_thread_for_user
        make_result(scalars_all=msgs),      # messages query
    ]
    out = await r.list_messages(
        thread_id=1, skip=0, limit=100, current_user=fake_user(), db=mock_db)
    assert len(out) == 2


# =========================================================================== #
# edit_message
# =========================================================================== #
async def test_edit_message_not_found(mock_db):
    thread = make_thread()
    mock_db.execute.side_effect = [
        make_result(scalar=thread),   # get_thread_for_user
        make_result(scalar=None),     # message lookup -> not found
    ]
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await r.edit_message(
                thread_id=1, message_id=5, data=r.ChatMessageEdit(content="new"),
                current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 404


async def test_edit_message_not_user_role(mock_db):
    thread = make_thread()
    msg = make_message(5, role="assistant")
    mock_db.execute.side_effect = [
        make_result(scalar=thread),
        make_result(scalar=msg),
    ]
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await r.edit_message(
                thread_id=1, message_id=5, data=r.ChatMessageEdit(content="new"),
                current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 400


async def test_edit_message_success(mock_db):
    thread = make_thread()
    msg = make_message(5, role="user", content="old")
    prev_assistant = make_message(3, role="assistant", interaction_id="ix-prev")
    mock_db.execute.side_effect = [
        make_result(scalar=thread),          # get_thread_for_user
        make_result(scalar=msg),             # message lookup
        make_result(),                       # delete messages after
        make_result(scalar=prev_assistant),  # previous assistant interaction
    ]
    gen = AsyncMock(return_value={"content": "regenerated"})
    new_msg = make_message(10, role="assistant", content="regenerated")
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()), \
         patch.object(r, "_create_assistant_message", return_value=new_msg), \
         patch("services.gemini_agent_service.generate_response", new=gen):
        out = await r.edit_message(
            thread_id=1, message_id=5, data=r.ChatMessageEdit(content="new content"),
            current_user=fake_user(), db=mock_db)
    assert msg.content == "new content"
    assert out.content == "regenerated"
    # generate_response called with previous interaction id
    assert gen.await_args.kwargs["previous_interaction_id"] == "ix-prev"


async def test_edit_message_generation_fails(mock_db):
    thread = make_thread()
    msg = make_message(5, role="user")
    mock_db.execute.side_effect = [
        make_result(scalar=thread),
        make_result(scalar=msg),
        make_result(),
        make_result(scalar=None),   # no previous assistant
    ]
    gen = AsyncMock(side_effect=RuntimeError("gen failed"))
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()), \
         patch("services.gemini_agent_service.generate_response", new=gen):
        with pytest.raises(HTTPException) as exc:
            await r.edit_message(
                thread_id=1, message_id=5, data=r.ChatMessageEdit(content="x"),
                current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 500
    mock_db.rollback.assert_awaited()


async def test_edit_message_generation_fails_rollback_also_fails(mock_db):
    thread = make_thread()
    msg = make_message(5, role="user")
    mock_db.execute.side_effect = [
        make_result(scalar=thread),
        make_result(scalar=msg),
        make_result(),
        make_result(scalar=None),
    ]
    gen = AsyncMock(side_effect=RuntimeError("gen failed"))
    mock_db.rollback = AsyncMock(side_effect=RuntimeError("rollback boom"))
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()), \
         patch("services.gemini_agent_service.generate_response", new=gen):
        with pytest.raises(HTTPException) as exc:
            await r.edit_message(
                thread_id=1, message_id=5, data=r.ChatMessageEdit(content="x"),
                current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 500


# =========================================================================== #
# regenerate_message
# =========================================================================== #
async def test_regenerate_message_not_found(mock_db):
    thread = make_thread()
    mock_db.execute.side_effect = [
        make_result(scalar=thread),
        make_result(scalar=None),   # message lookup
    ]
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await r.regenerate_message(
                thread_id=1, message_id=5, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 404


async def test_regenerate_message_not_assistant(mock_db):
    thread = make_thread()
    msg = make_message(5, role="user")
    mock_db.execute.side_effect = [
        make_result(scalar=thread),
        make_result(scalar=msg),
    ]
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await r.regenerate_message(
                thread_id=1, message_id=5, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 400


async def test_regenerate_message_no_user_message(mock_db):
    thread = make_thread()
    msg = make_message(5, role="assistant")
    mock_db.execute.side_effect = [
        make_result(scalar=thread),
        make_result(scalar=msg),
        make_result(scalar=None),   # no prior user message
    ]
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await r.regenerate_message(
                thread_id=1, message_id=5, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 400


async def test_regenerate_message_success(mock_db):
    thread = make_thread()
    msg = make_message(5, role="assistant")
    user_msg = make_message(4, role="user", content="original question")
    prev_assistant = make_message(2, role="assistant", interaction_id="ix-old")
    mock_db.execute.side_effect = [
        make_result(scalar=thread),          # get_thread_for_user
        make_result(scalar=msg),             # message lookup
        make_result(scalar=user_msg),        # prior user message
        make_result(scalar=prev_assistant),  # prev assistant interaction
        make_result(),                       # delete
    ]
    gen = AsyncMock(return_value={"content": "regenerated answer"})
    new_msg = make_message(11, role="assistant", content="regenerated answer")
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()), \
         patch.object(r, "_create_assistant_message", return_value=new_msg), \
         patch("services.gemini_agent_service.generate_response", new=gen):
        out = await r.regenerate_message(
            thread_id=1, message_id=5, current_user=fake_user(), db=mock_db)
    assert out.content == "regenerated answer"
    assert gen.await_args.kwargs["query"] == "original question"
    assert gen.await_args.kwargs["previous_interaction_id"] == "ix-old"


async def test_regenerate_message_generation_fails(mock_db):
    thread = make_thread()
    msg = make_message(5, role="assistant")
    user_msg = make_message(4, role="user")
    mock_db.execute.side_effect = [
        make_result(scalar=thread),
        make_result(scalar=msg),
        make_result(scalar=user_msg),
        make_result(scalar=None),   # no prev assistant
        make_result(),              # delete
    ]
    gen = AsyncMock(side_effect=RuntimeError("boom"))
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()), \
         patch("services.gemini_agent_service.generate_response", new=gen):
        with pytest.raises(HTTPException) as exc:
            await r.regenerate_message(
                thread_id=1, message_id=5, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 500
    mock_db.rollback.assert_awaited()


async def test_regenerate_message_generation_fails_rollback_also_fails(mock_db):
    thread = make_thread()
    msg = make_message(5, role="assistant")
    user_msg = make_message(4, role="user")
    mock_db.execute.side_effect = [
        make_result(scalar=thread),
        make_result(scalar=msg),
        make_result(scalar=user_msg),
        make_result(scalar=None),
        make_result(),
    ]
    gen = AsyncMock(side_effect=RuntimeError("boom"))
    mock_db.rollback = AsyncMock(side_effect=RuntimeError("rollback boom"))
    with patch.object(r, "_check_rate_limit_async", new=AsyncMock()), \
         patch("services.gemini_agent_service.generate_response", new=gen):
        with pytest.raises(HTTPException) as exc:
            await r.regenerate_message(
                thread_id=1, message_id=5, current_user=fake_user(), db=mock_db)
    assert exc.value.status_code == 500
