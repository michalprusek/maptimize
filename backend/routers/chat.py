"""Chat API routes for RAG-powered conversations."""
import logging
import time
from collections import defaultdict
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, distinct, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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

router = APIRouter()


# ============== Rate Limiting ==============
# Simple in-memory rate limiter for AI endpoints
# For production, consider using Redis-based rate limiting

# Rate limit: max 10 AI requests per minute per user
AI_RATE_LIMIT_REQUESTS = 10
AI_RATE_LIMIT_WINDOW = 60  # seconds

# Store: user_id -> list of timestamps
_rate_limit_store: dict[int, list[float]] = defaultdict(list)


def _check_rate_limit(user_id: int) -> None:
    """
    Check if user has exceeded rate limit for AI endpoints.

    Raises HTTPException 429 if rate limit exceeded.
    """
    now = time.time()
    window_start = now - AI_RATE_LIMIT_WINDOW

    # Clean old entries and get recent requests
    user_requests = _rate_limit_store[user_id]
    user_requests[:] = [ts for ts in user_requests if ts > window_start]

    if len(user_requests) >= AI_RATE_LIMIT_REQUESTS:
        # Calculate time until oldest request expires
        oldest = min(user_requests)
        retry_after = int(oldest + AI_RATE_LIMIT_WINDOW - now) + 1
        logger.warning(f"Rate limit exceeded for user {user_id}: {len(user_requests)} requests in {AI_RATE_LIMIT_WINDOW}s")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Maximum {AI_RATE_LIMIT_REQUESTS} AI requests per minute.",
            headers={"Retry-After": str(retry_after)},
        )

    # Record this request
    user_requests.append(now)


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
    _check_rate_limit(current_user.id)

    thread = await get_thread_for_user(db, thread_id, current_user.id)

    # Fetch conversation history (last N messages for context)
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

        # Rollback any pending transaction state from generate_response
        # to ensure clean state before inserting assistant message
        try:
            await db.rollback()
        except Exception as rollback_err:
            # Log rollback errors instead of silently ignoring
            logger.warning(f"Rollback after AI generation: {rollback_err}")

        # Create assistant message in a fresh transaction
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

        # Rollback any pending transaction state from generate_response
        try:
            await db.rollback()
        except Exception as rollback_err:
            logger.warning(f"Rollback error: {rollback_err}")

        # Create new assistant message in a fresh transaction
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
    _check_rate_limit(current_user.id)

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

        # Rollback any pending transaction state from generate_response
        try:
            await db.rollback()
        except Exception as rollback_err:
            logger.warning(f"Rollback error: {rollback_err}")

        # Create new assistant message in a fresh transaction
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
