"""Chat API routes for RAG-powered conversations."""
import logging
import time
from collections import defaultdict
from typing import List, Optional

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, distinct, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import get_settings
from database import get_db
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
)
from utils.security import get_current_user

logger = logging.getLogger(__name__)
settings = get_settings()

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


@router.post("/threads/{thread_id}/messages", response_model=ChatMessageResponse)
async def send_message(
    thread_id: int,
    data: ChatMessageCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Send a message and get AI response.

    Creates a user message and triggers the Gemini agent to generate a response.
    The response is generated synchronously and returned.
    """
    # Check rate limit before any processing
    await _check_rate_limit_async(current_user.id)

    thread = await get_thread_for_user(db, thread_id, current_user.id)

    # Fetch conversation history (last N messages for context)
    # Count total messages for token monitoring
    total_msg_count = (await db.execute(
        select(func.count(ChatMessage.id)).where(ChatMessage.thread_id == thread_id)
    )).scalar() or 0

    history_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(20)  # Limit history to avoid token limits
    )
    history_messages = history_result.scalars().all()
    history = [
        {"role": msg.role, "content": msg.content}
        for msg in history_messages
    ]

    # Estimate token usage (rough: ~4 chars per token)
    total_chars = sum(len(msg.content) for msg in history_messages)
    estimated_tokens = total_chars // 4
    context_warning = None
    if total_msg_count > 20:
        context_warning = f"Long conversation: showing last 20 of {total_msg_count} messages. Earlier context may be lost."
    elif estimated_tokens > 50000:
        context_warning = f"Large context (~{estimated_tokens//1000}k tokens). Consider starting a new conversation for complex queries."

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

    # Commit user message BEFORE AI generation to ensure it's saved
    # even if AI generation fails
    await db.commit()

    # Refresh to get the committed state
    await db.refresh(user_message)

    # Generate AI response using Gemini agent
    try:
        from services.gemini_agent_service import generate_response

        response_data = await generate_response(
            query=data.content,
            user_id=current_user.id,
            thread_id=thread_id,
            db=db,
            history=history,
        )

        # Add context warning if applicable
        content = response_data["content"]
        if context_warning:
            content = f"⚠️ *{context_warning}*\n\n{content}"

        # Create assistant message (generate_response commits its own changes)
        assistant_message = ChatMessage(
            thread_id=thread_id,
            role="assistant",
            content=content,
            citations=response_data.get("citations", []),
            image_refs=response_data.get("image_refs", []),
            tool_calls=response_data.get("tool_calls", []),
        )
        db.add(assistant_message)
        await db.commit()

        return ChatMessageResponse.model_validate(assistant_message)

    except Exception as e:
        logger.exception(f"Error generating AI response for thread {thread_id}")
        # Ensure transaction is rolled back to clean state
        try:
            await db.rollback()
        except Exception as rollback_err:
            logger.warning(f"Rollback error during error recovery: {rollback_err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate response: {str(e)}"
        )


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

    # Get conversation history (messages before this one, by ID)
    history_result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.thread_id == thread_id,
            ChatMessage.id < message.id
        )
        .order_by(ChatMessage.id.asc())
        .limit(20)
    )
    history_messages = history_result.scalars().all()
    history = [
        {"role": msg.role, "content": msg.content}
        for msg in history_messages
    ]

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
            history=history,
        )

        # Create new assistant message
        assistant_message = ChatMessage(
            thread_id=thread_id,
            role="assistant",
            content=response_data["content"],
            citations=response_data.get("citations", []),
            image_refs=response_data.get("image_refs", []),
            tool_calls=response_data.get("tool_calls", []),
        )
        db.add(assistant_message)
        await db.commit()

        return ChatMessageResponse.model_validate(assistant_message)

    except Exception as e:
        logger.exception(f"Error regenerating AI response for edited message")
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

    # Delete this message and all messages after it (by ID)
    await db.execute(
        delete(ChatMessage).where(
            ChatMessage.thread_id == thread_id,
            ChatMessage.id >= message.id
        )
    )

    # Get conversation history (messages before the user message, by ID)
    history_result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.thread_id == thread_id,
            ChatMessage.id < user_message.id
        )
        .order_by(ChatMessage.id.asc())
        .limit(20)
    )
    history_messages = history_result.scalars().all()
    history = [
        {"role": msg.role, "content": msg.content}
        for msg in history_messages
    ]

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
            history=history,
        )

        # Create new assistant message
        assistant_message = ChatMessage(
            thread_id=thread_id,
            role="assistant",
            content=response_data["content"],
            citations=response_data.get("citations", []),
            image_refs=response_data.get("image_refs", []),
            tool_calls=response_data.get("tool_calls", []),
        )
        db.add(assistant_message)
        await db.commit()

        return ChatMessageResponse.model_validate(assistant_message)

    except Exception as e:
        logger.exception(f"Error regenerating AI response")
        try:
            await db.rollback()
        except Exception as rollback_err:
            logger.warning(f"Rollback error: {rollback_err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate response: {str(e)}"
        )
