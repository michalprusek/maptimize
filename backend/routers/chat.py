"""Chat API routes for RAG-powered conversations."""
import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import get_settings
from database import get_db, async_session_maker
from models.user import User
from models.chat import ChatThread, ChatMessage
from schemas.chat import (
    ChatThreadCreate,
    ChatThreadUpdate,
    ChatThreadResponse,
    ChatThreadDetailResponse,
    ChatMessageCreate,
    ChatMessageEdit,
    ChatMessageResponse,
    GenerationStatusResponse,
    SendMessageResponse,
)
from utils.security import get_current_user

logger = logging.getLogger(__name__)
settings = get_settings()

# Track active generation tasks (in-memory for this process)
_active_tasks: Dict[str, asyncio.Task] = {}

router = APIRouter()


# ============== Rate Limiting (Redis-based for production) ==============

# Rate limit: max 10 AI requests per minute per user
AI_RATE_LIMIT_REQUESTS = 10
AI_RATE_LIMIT_WINDOW = 60  # seconds

# Redis connection pool (lazy initialization)
_redis_pool: Optional[redis.Redis] = None


async def _get_redis() -> redis.Redis:
    """Get Redis connection (lazy initialization with connection pool)."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=10,
        )
    return _redis_pool


async def _check_rate_limit_async(user_id: int) -> None:
    """
    Check if user has exceeded rate limit for AI endpoints using Redis.

    Uses Redis sorted sets for efficient sliding window rate limiting.
    This implementation is production-ready: works across multiple workers,
    survives restarts, and handles distributed deployments.

    Raises HTTPException 429 if rate limit exceeded.
    """
    try:
        r = await _get_redis()
        key = f"rate_limit:chat:{user_id}"
        now = time.time()
        window_start = now - AI_RATE_LIMIT_WINDOW

        # Use Redis transaction (pipeline) for atomic operations
        async with r.pipeline(transaction=True) as pipe:
            # Remove old entries outside the window
            pipe.zremrangebyscore(key, 0, window_start)
            # Count current requests in window
            pipe.zcard(key)
            # Execute atomically
            results = await pipe.execute()

        request_count = results[1]

        if request_count >= AI_RATE_LIMIT_REQUESTS:
            # Get oldest timestamp to calculate retry-after
            oldest_entries = await r.zrange(key, 0, 0, withscores=True)
            if oldest_entries:
                oldest_ts = oldest_entries[0][1]
                retry_after = int(oldest_ts + AI_RATE_LIMIT_WINDOW - now) + 1
            else:
                retry_after = AI_RATE_LIMIT_WINDOW

            logger.warning(f"Rate limit exceeded for user {user_id}: {request_count} requests in {AI_RATE_LIMIT_WINDOW}s")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Maximum {AI_RATE_LIMIT_REQUESTS} AI requests per minute.",
                headers={"Retry-After": str(retry_after)},
            )

        # Record this request with current timestamp as score
        # Use unique member to avoid collisions (timestamp + random suffix)
        import uuid
        member = f"{now}:{uuid.uuid4().hex[:8]}"
        await r.zadd(key, {member: now})

        # Set TTL on key to auto-cleanup (window + buffer)
        await r.expire(key, AI_RATE_LIMIT_WINDOW + 10)

    except redis.RedisError as e:
        # If Redis is unavailable, log warning but allow request (fail-open)
        # In production, you might want fail-closed behavior instead
        logger.warning(f"Redis rate limit check failed, allowing request: {e}")


def _check_rate_limit(user_id: int) -> None:
    """
    Synchronous wrapper for rate limit check (backwards compatibility).

    For new async endpoints, use _check_rate_limit_async directly.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're already in an async context, create task
            # This shouldn't happen often, prefer using _check_rate_limit_async
            asyncio.create_task(_check_rate_limit_async(user_id))
        else:
            loop.run_until_complete(_check_rate_limit_async(user_id))
    except RuntimeError:
        # No event loop, use asyncio.run
        asyncio.run(_check_rate_limit_async(user_id))


async def get_thread_for_user(
    db: AsyncSession,
    thread_id: int,
    user_id: int
) -> ChatThread:
    """Get chat thread and verify ownership. Raises 404 if not found."""
    result = await db.execute(
        select(ChatThread).where(
            ChatThread.id == thread_id,
            ChatThread.user_id == user_id
        )
    )
    thread = result.scalar_one_or_none()
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found"
        )
    return thread


def _create_assistant_message(
    thread_id: int,
    response_data: Dict[str, Any],
    content_override: Optional[str] = None,
) -> ChatMessage:
    """Create a ChatMessage from generate_response output.

    Args:
        thread_id: The thread ID for the message
        response_data: Dict from generate_response with content, citations, etc.
        content_override: Optional content to use instead of response_data["content"]

    Returns:
        ChatMessage instance (not yet added to session)
    """
    return ChatMessage(
        thread_id=thread_id,
        role="assistant",
        content=content_override or response_data["content"],
        citations=response_data.get("citations", []),
        image_refs=response_data.get("image_refs", []),
        tool_calls=response_data.get("tool_calls", []),
        interaction_id=response_data.get("interaction_id"),
    )


# ============== Async Generation Task Management ==============

async def _run_generation_task(
    task_id: str,
    thread_id: int,
    user_id: int,
    query: str,
    previous_interaction_id: Optional[str] = None,
    context_warning: Optional[str] = None,
):
    """
    Background task to generate AI response asynchronously.

    CRITICAL: This runs in a separate asyncio task with its own database session.
    Do not rely on the parent request's DB session or transaction.

    Workflow:
    1. Creates isolated DB session via async_session_maker()
    2. Re-validates user authorization (user may be deleted during generation)
    3. Updates thread status to "generating" and commits
    4. Checks Redis for cancellation flag (before generation)
    5. Calls gemini_agent_service.generate_response()
    6. Checks Redis for cancellation flag (during generation)
    7. Creates assistant message and updates thread to "completed"
    8. Cleans up task reference and Redis flags in finally block

    Cancellation: Uses Redis flag (cancel:{task_id}) for cross-process coordination.
    """
    try:
        # Create new DB session for this task
        async with async_session_maker() as db:
            # Re-validate user authorization (user may be deleted during generation)
            from models.user import User
            user = await db.get(User, user_id)
            if not user:
                logger.error(f"Task {task_id}: User {user_id} not found (deleted during generation?)")
                return

            # Update thread status to generating
            thread = await db.get(ChatThread, thread_id)
            if not thread:
                logger.error(f"Task {task_id}: Thread {thread_id} not found")
                return

            # Re-validate thread ownership
            if thread.user_id != user_id:
                logger.error(f"Task {task_id}: Thread {thread_id} ownership changed (user {user_id} != {thread.user_id})")
                return

            thread.generation_status = "generating"
            thread.generation_task_id = task_id
            thread.generation_started_at = datetime.now(timezone.utc)
            thread.generation_error = None
            await db.commit()

            # Check if cancelled before starting
            r = await _get_redis()
            if await r.get(f"cancel:{task_id}"):
                thread.generation_status = "cancelled"
                thread.generation_task_id = None
                await db.commit()
                logger.info(f"Task {task_id}: Cancelled before start")
                return

            try:
                from services.gemini_agent_service import generate_response

                response_data = await generate_response(
                    query=query,
                    user_id=user_id,
                    thread_id=thread_id,
                    db=db,
                    previous_interaction_id=previous_interaction_id,
                )

                # Check if cancelled during generation
                if await r.get(f"cancel:{task_id}"):
                    thread.generation_status = "cancelled"
                    thread.generation_task_id = None
                    await db.commit()
                    logger.info(f"Task {task_id}: Cancelled during generation")
                    return

                # Add context warning if applicable
                content_with_warning = None
                if context_warning:
                    content_with_warning = f"⚠️ *{context_warning}*\n\n{response_data['content']}"

                assistant_message = _create_assistant_message(
                    thread_id, response_data, content_override=content_with_warning
                )
                db.add(assistant_message)

                # Update thread status to completed
                thread.generation_status = "completed"
                thread.generation_task_id = None
                await db.commit()

                logger.info(f"Task {task_id}: Generation completed successfully")

            except asyncio.CancelledError:
                thread.generation_status = "cancelled"
                thread.generation_task_id = None
                await db.commit()
                logger.info(f"Task {task_id}: Cancelled via CancelledError")

            except Exception as e:
                logger.exception(f"Task {task_id}: Generation error: {e}")
                thread.generation_status = "error"
                thread.generation_error = str(e)[:500]
                thread.generation_task_id = None
                await db.commit()

    except Exception as e:
        logger.exception(f"Task {task_id}: Fatal error in generation task: {e}")
        # Try to update thread status to error if possible
        try:
            async with async_session_maker() as error_db:
                thread = await error_db.get(ChatThread, thread_id)
                if thread and thread.generation_status == "generating":
                    thread.generation_status = "error"
                    thread.generation_error = f"Task initialization failed: {str(e)[:500]}"
                    thread.generation_task_id = None
                    await error_db.commit()
                    logger.info(f"Task {task_id}: Updated thread {thread_id} status to error after fatal failure")
        except Exception as db_error:
            logger.error(f"Task {task_id}: Failed to update thread status after fatal error: {db_error}")
    finally:
        # Clean up task reference
        _active_tasks.pop(task_id, None)
        # Clean up Redis cancel flag
        try:
            r = await _get_redis()
            await r.delete(f"cancel:{task_id}")
        except redis.RedisError as redis_err:
            logger.warning(f"Task {task_id}: Failed to clean up Redis cancel flag: {redis_err}")
        except Exception as cleanup_err:
            logger.error(f"Task {task_id}: Unexpected error during Redis cleanup: {cleanup_err}")


async def _cancel_generation(task_id: str) -> bool:
    """
    Cancel a generation task.

    Sets cancellation flag in Redis with 5-minute TTL for cross-process coordination.
    Also attempts to cancel the asyncio task if it exists in this process.

    Returns:
        bool: True if an asyncio task was found and cancel() was called on it.
              False if task not found or already done. This does NOT guarantee
              cancellation succeeded - check thread.generation_status instead.

    Raises:
        HTTPException: If Redis operation fails.
    """
    # Set cancellation flag in Redis
    try:
        r = await _get_redis()
        await r.setex(f"cancel:{task_id}", 300, "1")  # 5 min TTL
        logger.info(f"Set cancellation flag in Redis for task {task_id}")
    except redis.RedisError as e:
        logger.error(f"Failed to set cancellation flag in Redis for task {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel generation due to server error. Please try again."
        )

    # Try to cancel the asyncio task
    task = _active_tasks.get(task_id)
    if task and not task.done():
        task.cancel()
        logger.info(f"Cancelled asyncio task for {task_id}")
        return True
    else:
        logger.info(f"Task {task_id} already completed or not found in memory (may be on different worker)")
        return False


@router.get("/threads", response_model=List[ChatThreadResponse])
async def list_threads(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List user's chat threads with message counts."""
    result = await db.execute(
        select(
            ChatThread,
            func.count(ChatMessage.id).label("message_count"),
        )
        .outerjoin(ChatMessage, ChatThread.id == ChatMessage.thread_id)
        .where(ChatThread.user_id == current_user.id)
        .group_by(ChatThread.id)
        .order_by(ChatThread.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = result.unique().all()

    response = []
    for thread, message_count in rows:
        thread_response = ChatThreadResponse.model_validate(thread)
        thread_response.message_count = message_count or 0
        response.append(thread_response)

    return response


@router.post("/threads", response_model=ChatThreadResponse, status_code=status.HTTP_201_CREATED)
async def create_thread(
    data: Optional[ChatThreadCreate] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new chat thread."""
    thread = ChatThread(
        user_id=current_user.id,
        name=data.name if data and data.name else "New Chat",
    )
    db.add(thread)
    await db.flush()

    logger.info(f"Created chat thread {thread.id} for user {current_user.id}")

    return ChatThreadResponse.model_validate(thread)


@router.get("/threads/{thread_id}", response_model=ChatThreadDetailResponse)
async def get_thread(
    thread_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get a chat thread with all messages."""
    result = await db.execute(
        select(ChatThread)
        .options(selectinload(ChatThread.messages))
        .where(
            ChatThread.id == thread_id,
            ChatThread.user_id == current_user.id
        )
    )
    thread = result.scalar_one_or_none()
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found"
        )

    response = ChatThreadDetailResponse.model_validate(thread)
    response.message_count = len(thread.messages)
    response.messages = [
        ChatMessageResponse.model_validate(msg)
        for msg in thread.messages
    ]

    return response


@router.patch("/threads/{thread_id}", response_model=ChatThreadResponse)
async def update_thread(
    thread_id: int,
    data: ChatThreadUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Rename a chat thread."""
    thread = await get_thread_for_user(db, thread_id, current_user.id)
    thread.name = data.name
    await db.commit()

    return ChatThreadResponse.model_validate(thread)


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a chat thread and all its messages."""
    thread = await get_thread_for_user(db, thread_id, current_user.id)
    await db.delete(thread)
    await db.commit()

    logger.info(f"Deleted chat thread {thread_id}")


@router.post("/threads/{thread_id}/messages", response_model=SendMessageResponse)
async def send_message(
    thread_id: int,
    data: ChatMessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Send a message and start async AI response generation.

    Creates a user message and triggers background generation.
    Returns immediately with the user message and generation task ID.
    Use GET /threads/{id}/generation-status to poll for completion.

    Uses row-level locking to prevent race conditions when multiple
    requests try to start generation simultaneously.
    """
    # Check rate limit before any processing
    await _check_rate_limit_async(current_user.id)

    # Use SELECT FOR UPDATE to acquire row lock and prevent race conditions
    # This ensures only one request can start generation at a time
    result = await db.execute(
        select(ChatThread)
        .where(
            ChatThread.id == thread_id,
            ChatThread.user_id == current_user.id
        )
        .with_for_update()
    )
    thread = result.scalar_one_or_none()
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat thread not found"
        )

    # Check if already generating (now safe due to row lock)
    if thread.generation_status == "generating":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A response is already being generated for this thread. Wait for completion or cancel it."
        )

    # Get the previous interaction_id from the last assistant message
    # This enables Gemini Interactions API server-side state management
    last_assistant_result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.thread_id == thread_id,
            ChatMessage.role == "assistant",
            ChatMessage.interaction_id.isnot(None)
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    last_assistant = last_assistant_result.scalar_one_or_none()
    previous_interaction_id = last_assistant.interaction_id if last_assistant else None

    # Check message count for context warning
    total_msg_count = (await db.execute(
        select(func.count(ChatMessage.id)).where(ChatMessage.thread_id == thread_id)
    )).scalar() or 0

    context_warning = None
    if total_msg_count > 50:
        context_warning = f"Long conversation ({total_msg_count} messages)."

    # Create user message
    user_message = ChatMessage(
        thread_id=thread_id,
        role="user",
        content=data.content,
    )
    db.add(user_message)

    # Update thread name from first message if still default
    if thread.name == "New Chat":
        thread.name = data.content[:50] + ("..." if len(data.content) > 50 else "")

    # Generate task ID and update thread status
    task_id = f"gen_{thread_id}_{uuid.uuid4().hex[:12]}"
    thread.generation_status = "generating"
    thread.generation_task_id = task_id
    thread.generation_started_at = datetime.now(timezone.utc)
    thread.generation_error = None

    await db.commit()
    await db.refresh(user_message)

    # Start background generation task with previous_interaction_id for context
    def _task_done_callback(t: asyncio.Task, tid: str = task_id):
        """Callback to ensure task is always cleaned up from _active_tasks."""
        _active_tasks.pop(tid, None)
        if t.cancelled():
            logger.info(f"Task {tid}: Cleaned up after cancellation")
        elif t.exception():
            logger.error(f"Task {tid}: Cleaned up after exception: {t.exception()}")

    task = asyncio.create_task(
        _run_generation_task(
            task_id=task_id,
            thread_id=thread_id,
            user_id=current_user.id,
            query=data.content,
            previous_interaction_id=previous_interaction_id,
            context_warning=context_warning,
        )
    )
    task.add_done_callback(_task_done_callback)
    _active_tasks[task_id] = task

    return SendMessageResponse(
        user_message=ChatMessageResponse.model_validate(user_message),
        generation_status="generating",
        task_id=task_id,
    )


@router.get("/threads/{thread_id}/generation-status", response_model=GenerationStatusResponse)
async def get_generation_status(
    thread_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get the current generation status for a thread.

    Poll this endpoint to check if AI response generation is complete.
    When status is "completed", the new message will be included in the response.
    """
    thread = await get_thread_for_user(db, thread_id, current_user.id)

    response = GenerationStatusResponse(
        thread_id=thread_id,
        status=thread.generation_status or "idle",
        task_id=thread.generation_task_id,
        started_at=thread.generation_started_at,
        error=thread.generation_error,
    )

    # Calculate elapsed time
    if thread.generation_started_at and thread.generation_status == "generating":
        elapsed = (datetime.now(timezone.utc) - thread.generation_started_at).total_seconds()
        response.elapsed_seconds = int(elapsed)

    # If completed, include the latest assistant message
    if thread.generation_status == "completed":
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread_id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        latest_message = result.scalar_one_or_none()
        if latest_message:
            response.message = ChatMessageResponse.model_validate(latest_message)

        # Reset status to idle after returning completed
        thread.generation_status = "idle"
        await db.commit()

    return response


@router.post("/threads/{thread_id}/cancel-generation")
async def cancel_generation(
    thread_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Cancel an ongoing AI response generation.

    Returns success if cancellation was initiated.
    """
    thread = await get_thread_for_user(db, thread_id, current_user.id)

    if thread.generation_status != "generating":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No generation in progress to cancel"
        )

    task_id = thread.generation_task_id
    if task_id:
        await _cancel_generation(task_id)

    # Update thread status
    thread.generation_status = "cancelled"
    thread.generation_task_id = None
    await db.commit()

    logger.info(f"Cancelled generation for thread {thread_id}, task {task_id}")

    return {"status": "cancelled", "thread_id": thread_id}


@router.get("/threads/{thread_id}/messages", response_model=List[ChatMessageResponse])
async def list_messages(
    thread_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get messages for a chat thread."""
    thread = await get_thread_for_user(db, thread_id, current_user.id)

    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.created_at.asc())
        .offset(skip)
        .limit(limit)
    )
    messages = result.scalars().all()

    return [ChatMessageResponse.model_validate(msg) for msg in messages]


@router.put("/threads/{thread_id}/messages/{message_id}", response_model=ChatMessageResponse)
async def edit_message(
    thread_id: int,
    message_id: int,
    data: ChatMessageEdit,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Edit a user message and regenerate AI response.

    This will:
    1. Delete all messages after the edited message
    2. Update the message content
    3. Regenerate the AI response
    """
    # Check rate limit before any processing (editing triggers AI regeneration)
    await _check_rate_limit_async(current_user.id)

    thread = await get_thread_for_user(db, thread_id, current_user.id)

    # Get the message to edit
    result = await db.execute(
        select(ChatMessage).where(
            ChatMessage.id == message_id,
            ChatMessage.thread_id == thread_id
        )
    )
    message = result.scalar_one_or_none()
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found"
        )

    # Only allow editing user messages
    if message.role != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only user messages can be edited"
        )

    # Delete all messages after this one (by ID, not timestamp)
    # Using ID is more reliable than created_at for ordering
    await db.execute(
        delete(ChatMessage).where(
            ChatMessage.thread_id == thread_id,
            ChatMessage.id > message.id
        )
    )

    # Update the message content
    message.content = data.content

    # Find the previous interaction_id from the last assistant message BEFORE the edited one
    # For edit, we start fresh context (or use the previous assistant's interaction_id)
    prev_assistant_result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.thread_id == thread_id,
            ChatMessage.id < message.id,
            ChatMessage.role == "assistant",
            ChatMessage.interaction_id.isnot(None)
        )
        .order_by(ChatMessage.id.desc())
        .limit(1)
    )
    prev_assistant = prev_assistant_result.scalar_one_or_none()
    previous_interaction_id = prev_assistant.interaction_id if prev_assistant else None

    # Commit the edit BEFORE AI generation to ensure it's saved
    await db.commit()

    # Generate AI response
    try:
        from services.gemini_agent_service import generate_response

        response_data = await generate_response(
            query=data.content,
            user_id=current_user.id,
            thread_id=thread_id,
            db=db,
            previous_interaction_id=previous_interaction_id,
        )

        assistant_message = _create_assistant_message(thread_id, response_data)
        db.add(assistant_message)
        await db.commit()

        return ChatMessageResponse.model_validate(assistant_message)

    except Exception as e:
        logger.exception("Error regenerating AI response for edited message")
        try:
            await db.rollback()
        except Exception as rollback_err:
            logger.warning(f"Rollback error: {rollback_err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate response: {str(e)}"
        )


@router.post("/threads/{thread_id}/messages/{message_id}/regenerate", response_model=ChatMessageResponse)
async def regenerate_message(
    thread_id: int,
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Regenerate an assistant message.

    This will:
    1. Delete the message and all messages after it
    2. Find the last user message before it
    3. Regenerate the AI response
    """
    # Check rate limit before any processing
    await _check_rate_limit_async(current_user.id)

    thread = await get_thread_for_user(db, thread_id, current_user.id)

    # Get the message to regenerate
    result = await db.execute(
        select(ChatMessage).where(
            ChatMessage.id == message_id,
            ChatMessage.thread_id == thread_id
        )
    )
    message = result.scalar_one_or_none()
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found"
        )

    # Only allow regenerating assistant messages
    if message.role != "assistant":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only assistant messages can be regenerated"
        )

    # Find the user message that triggered this response (the one right before by ID)
    user_msg_result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.thread_id == thread_id,
            ChatMessage.id < message.id,
            ChatMessage.role == "user"
        )
        .order_by(ChatMessage.id.desc())
        .limit(1)
    )
    user_message = user_msg_result.scalar_one_or_none()
    if not user_message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No user message found before this assistant message"
        )

    # Find the previous interaction_id from the assistant message BEFORE the one being regenerated
    # For regenerate, we use the interaction_id from the previous assistant message
    prev_assistant_result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.thread_id == thread_id,
            ChatMessage.id < message.id,
            ChatMessage.role == "assistant",
            ChatMessage.interaction_id.isnot(None)
        )
        .order_by(ChatMessage.id.desc())
        .limit(1)
    )
    prev_assistant = prev_assistant_result.scalar_one_or_none()
    previous_interaction_id = prev_assistant.interaction_id if prev_assistant else None

    # Delete this message and all messages after it (by ID)
    await db.execute(
        delete(ChatMessage).where(
            ChatMessage.thread_id == thread_id,
            ChatMessage.id >= message.id
        )
    )

    # Store user message content before committing (in case it gets detached)
    user_query = user_message.content

    # Commit the deletion BEFORE AI generation
    await db.commit()

    # Generate new AI response
    try:
        from services.gemini_agent_service import generate_response

        response_data = await generate_response(
            query=user_query,
            user_id=current_user.id,
            thread_id=thread_id,
            db=db,
            previous_interaction_id=previous_interaction_id,
        )

        assistant_message = _create_assistant_message(thread_id, response_data)
        db.add(assistant_message)
        await db.commit()

        return ChatMessageResponse.model_validate(assistant_message)

    except Exception as e:
        logger.exception("Error regenerating AI response")
        try:
            await db.rollback()
        except Exception as rollback_err:
            logger.warning(f"Rollback error: {rollback_err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate response: {str(e)}"
        )
