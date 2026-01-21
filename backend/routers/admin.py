"""Admin panel API endpoints."""
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User, UserRole
from models.experiment import Experiment
from models.image import Image
from models.chat import ChatThread, ChatMessage
from models.rag_document import RAGDocument
from schemas.admin import (
    AdminSystemStats,
    AdminTimelineStats,
    AdminTimelinePoint,
    AdminUserListResponse,
    AdminUserListItem,
    AdminUserDetail,
    AdminUserUpdate,
    AdminPasswordResetResponse,
    AdminChatThreadListResponse,
    AdminChatThread,
    AdminChatMessagesResponse,
    AdminChatMessage,
    AdminExperimentsResponse,
    AdminExperiment,
)
from utils.security import get_current_admin, hash_password

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_user_or_404(db: AsyncSession, user_id: int) -> User:
    """Fetch user by ID or raise 404 if not found."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return user


@router.get("/stats", response_model=AdminSystemStats)
async def get_system_stats(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get system-wide statistics."""
    # User counts by role
    role_counts = await db.execute(
        select(User.role, func.count(User.id))
        .group_by(User.role)
    )
    role_stats = {role.value: count for role, count in role_counts}

    # Total users
    total_users = sum(role_stats.values())

    # Experiment count
    exp_result = await db.execute(select(func.count(Experiment.id)))
    total_experiments = exp_result.scalar() or 0

    # Image count and storage
    img_result = await db.execute(
        select(
            func.count(Image.id),
            func.coalesce(func.sum(Image.file_size), 0)
        )
    )
    img_row = img_result.one()
    total_images = img_row[0] or 0
    images_storage = img_row[1] or 0

    # Document count and storage
    doc_result = await db.execute(
        select(
            func.count(RAGDocument.id),
            func.coalesce(func.sum(RAGDocument.file_size), 0)
        )
    )
    doc_row = doc_result.one()
    total_documents = doc_row[0] or 0
    documents_storage = doc_row[1] or 0

    return AdminSystemStats(
        total_users=total_users,
        total_experiments=total_experiments,
        total_images=total_images,
        total_documents=total_documents,
        total_storage_bytes=images_storage + documents_storage,
        admin_count=role_stats.get("admin", 0),
        researcher_count=role_stats.get("researcher", 0),
        viewer_count=role_stats.get("viewer", 0),
        images_storage_bytes=images_storage,
        documents_storage_bytes=documents_storage,
    )


@router.get("/stats/timeline", response_model=AdminTimelineStats)
async def get_timeline_stats(
    days: int = Query(30, ge=7, le=90),
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get timeline statistics for charts (registrations and activity)."""
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days)

    # Get registrations per day
    registrations = await db.execute(
        select(
            func.date(User.created_at).label("date"),
            func.count(User.id).label("count")
        )
        .where(User.created_at >= start_date)
        .group_by(func.date(User.created_at))
    )
    reg_by_date = {str(row.date): row.count for row in registrations}

    # Get active users per day (based on last_login)
    active = await db.execute(
        select(
            func.date(User.last_login).label("date"),
            func.count(User.id).label("count")
        )
        .where(User.last_login >= start_date)
        .group_by(func.date(User.last_login))
    )
    active_by_date = {str(row.date): row.count for row in active}

    # Build timeline data
    data = []
    current = start_date.date()
    end = now.date()
    while current <= end:
        date_str = str(current)
        data.append(AdminTimelinePoint(
            date=date_str,
            registrations=reg_by_date.get(date_str, 0),
            active_users=active_by_date.get(date_str, 0),
        ))
        current += timedelta(days=1)

    return AdminTimelineStats(data=data, period_days=days)


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=10, le=100),
    search: Optional[str] = Query(None, min_length=1, max_length=100),
    role: Optional[UserRole] = None,
    sort_by: str = Query("created_at", regex="^(created_at|last_login|name|email)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all users with pagination and filtering."""
    # Base query
    query = select(User)

    # Search filter
    if search:
        search_filter = or_(
            User.email.ilike(f"%{search}%"),
            User.name.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)

    # Role filter
    if role:
        query = query.where(User.role == role)

    # Count total before pagination
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Sorting
    sort_column = getattr(User, sort_by)
    if sort_order == "desc":
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    # Pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    users = result.scalars().all()

    # Get counts and storage for each user
    user_ids = [u.id for u in users]

    # Experiment counts
    exp_counts = {}
    if user_ids:
        exp_result = await db.execute(
            select(Experiment.user_id, func.count(Experiment.id))
            .where(Experiment.user_id.in_(user_ids))
            .group_by(Experiment.user_id)
        )
        exp_counts = {row[0]: row[1] for row in exp_result}

    # Image counts and storage (via experiment)
    img_stats = {}
    if user_ids:
        img_result = await db.execute(
            select(
                Experiment.user_id,
                func.count(Image.id),
                func.coalesce(func.sum(Image.file_size), 0)
            )
            .join(Image, Image.experiment_id == Experiment.id)
            .where(Experiment.user_id.in_(user_ids))
            .group_by(Experiment.user_id)
        )
        img_stats = {row[0]: (row[1], row[2]) for row in img_result}

    # Document storage
    doc_storage = {}
    if user_ids:
        doc_result = await db.execute(
            select(
                RAGDocument.user_id,
                func.coalesce(func.sum(RAGDocument.file_size), 0)
            )
            .where(RAGDocument.user_id.in_(user_ids))
            .group_by(RAGDocument.user_id)
        )
        doc_storage = {row[0]: row[1] for row in doc_result}

    # Build response
    user_items = []
    for user in users:
        img_count, img_storage = img_stats.get(user.id, (0, 0))
        doc_stor = doc_storage.get(user.id, 0)
        user_items.append(AdminUserListItem(
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            avatar_url=user.avatar_url,
            created_at=user.created_at,
            last_login=user.last_login,
            experiment_count=exp_counts.get(user.id, 0),
            image_count=img_count,
            storage_bytes=img_storage + doc_stor,
        ))

    total_pages = (total + page_size - 1) // page_size

    return AdminUserListResponse(
        users=user_items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_user_detail(
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed info about a specific user."""
    user = await get_user_or_404(db, user_id)

    # Count experiments
    exp_result = await db.execute(
        select(func.count(Experiment.id))
        .where(Experiment.user_id == user_id)
    )
    experiment_count = exp_result.scalar() or 0

    # Count images and storage (via experiments)
    img_result = await db.execute(
        select(
            func.count(Image.id),
            func.coalesce(func.sum(Image.file_size), 0)
        )
        .join(Experiment, Image.experiment_id == Experiment.id)
        .where(Experiment.user_id == user_id)
    )
    img_row = img_result.one()
    image_count = img_row[0] or 0
    images_storage = img_row[1] or 0

    # Count documents and storage
    doc_result = await db.execute(
        select(
            func.count(RAGDocument.id),
            func.coalesce(func.sum(RAGDocument.file_size), 0)
        )
        .where(RAGDocument.user_id == user_id)
    )
    doc_row = doc_result.one()
    document_count = doc_row[0] or 0
    documents_storage = doc_row[1] or 0

    # Count chat threads
    chat_result = await db.execute(
        select(func.count(ChatThread.id))
        .where(ChatThread.user_id == user_id)
    )
    chat_thread_count = chat_result.scalar() or 0

    return AdminUserDetail(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        avatar_url=user.avatar_url,
        created_at=user.created_at,
        last_login=user.last_login,
        experiment_count=experiment_count,
        image_count=image_count,
        document_count=document_count,
        chat_thread_count=chat_thread_count,
        images_storage_bytes=images_storage,
        documents_storage_bytes=documents_storage,
        total_storage_bytes=images_storage + documents_storage,
    )


@router.patch("/users/{user_id}", response_model=AdminUserDetail)
async def update_user(
    user_id: int,
    update_data: AdminUserUpdate,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update user details (name, role)."""
    user = await get_user_or_404(db, user_id)

    # Prevent admin from demoting themselves
    if user_id == current_admin.id and update_data.role and update_data.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own admin role"
        )

    # Update fields
    if update_data.name is not None:
        user.name = update_data.name
    if update_data.role is not None:
        user.role = update_data.role

    await db.commit()
    await db.refresh(user)

    # Return full detail (reuse the detail endpoint logic)
    return await get_user_detail(user_id, current_admin, db)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a user and all their data (CASCADE)."""
    # Prevent self-deletion
    if user_id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )

    user = await get_user_or_404(db, user_id)

    # Delete user (cascades to experiments, images, chats, etc.)
    await db.delete(user)
    await db.commit()

    logger.info(f"Admin {current_admin.email} deleted user {user.email} (id={user_id})")


@router.post("/users/{user_id}/reset-password", response_model=AdminPasswordResetResponse)
async def reset_user_password(
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reset user password to a random value."""
    user = await get_user_or_404(db, user_id)

    # Generate random password (12 chars, alphanumeric + special)
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    new_password = ''.join(secrets.choice(alphabet) for _ in range(12))

    # Hash and save
    user.password_hash = hash_password(new_password)
    await db.commit()

    logger.info(f"Admin {current_admin.email} reset password for user {user.email}")

    return AdminPasswordResetResponse(new_password=new_password)


@router.get("/users/{user_id}/conversations", response_model=AdminChatThreadListResponse)
async def get_user_conversations(
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get all chat threads for a user."""
    await get_user_or_404(db, user_id)

    # Get threads with message count
    result = await db.execute(
        select(
            ChatThread,
            func.count(ChatMessage.id).label("message_count")
        )
        .outerjoin(ChatMessage, ChatMessage.thread_id == ChatThread.id)
        .where(ChatThread.user_id == user_id)
        .group_by(ChatThread.id)
        .order_by(ChatThread.updated_at.desc())
    )

    threads = []
    for row in result:
        thread = row[0]
        message_count = row[1]
        threads.append(AdminChatThread(
            id=thread.id,
            name=thread.name,
            message_count=message_count,
            created_at=thread.created_at,
            updated_at=thread.updated_at,
        ))

    return AdminChatThreadListResponse(
        threads=threads,
        total=len(threads),
    )


@router.get("/users/{user_id}/conversations/{thread_id}", response_model=AdminChatMessagesResponse)
async def get_conversation_messages(
    user_id: int,
    thread_id: int,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get messages in a chat thread."""
    # Verify thread exists and belongs to user
    result = await db.execute(
        select(ChatThread)
        .where(and_(ChatThread.id == thread_id, ChatThread.user_id == user_id))
    )
    thread = result.scalar_one_or_none()

    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )

    # Get messages
    msg_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.created_at.asc())
    )
    messages = msg_result.scalars().all()

    msg_items = [
        AdminChatMessage(
            id=msg.id,
            role=msg.role,
            content=msg.content,
            created_at=msg.created_at,
            has_citations=bool(msg.citations),
            has_images=bool(msg.image_refs),
        )
        for msg in messages
    ]

    return AdminChatMessagesResponse(
        messages=msg_items,
        thread_name=thread.name,
        total=len(msg_items),
    )


@router.get("/users/{user_id}/experiments", response_model=AdminExperimentsResponse)
async def get_user_experiments(
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get all experiments for a user."""
    await get_user_or_404(db, user_id)

    # Get experiments with image count
    result = await db.execute(
        select(
            Experiment,
            func.count(Image.id).label("image_count")
        )
        .outerjoin(Image, Image.experiment_id == Experiment.id)
        .where(Experiment.user_id == user_id)
        .group_by(Experiment.id)
        .order_by(Experiment.updated_at.desc())
    )

    experiments = []
    for row in result:
        exp = row[0]
        image_count = row[1]
        experiments.append(AdminExperiment(
            id=exp.id,
            name=exp.name,
            description=exp.description,
            status=exp.status.value,
            image_count=image_count,
            created_at=exp.created_at,
            updated_at=exp.updated_at,
        ))

    return AdminExperimentsResponse(
        experiments=experiments,
        total=len(experiments),
    )
