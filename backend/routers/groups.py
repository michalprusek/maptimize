"""Group routes - Shared experiments and metrics."""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.user import User
from models.group import Group, GroupMember
from schemas.group import (
    GroupCreate,
    GroupUpdate,
    GroupMemberResponse,
    GroupResponse,
    GroupDetailResponse,
    GroupListResponse,
    MyGroupResponse,
)
from utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Helper Functions
# =============================================================================


async def get_user_group_membership(
    db: AsyncSession,
    user_id: int
) -> Optional[GroupMember]:
    """Get user's group membership, or None if not in a group."""
    result = await db.execute(
        select(GroupMember).where(GroupMember.user_id == user_id)
    )
    return result.scalar_one_or_none()


def build_group_response(group: Group, member_count: int) -> GroupResponse:
    """Build GroupResponse from a Group and member count."""
    return GroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        created_by_user_id=group.created_by_user_id,
        creator_name=group.creator.name if group.creator else "Unknown",
        member_count=member_count,
        created_at=group.created_at,
    )


def build_group_detail_response(group: Group) -> GroupDetailResponse:
    """Build GroupDetailResponse from a Group with loaded members."""
    members = []
    for m in group.members:
        members.append(GroupMemberResponse(
            id=m.id,
            user_id=m.user_id,
            user_name=m.user.name if m.user else "Unknown",
            user_email=m.user.email if m.user else "",
            role=m.role,
            joined_at=m.joined_at,
        ))

    return GroupDetailResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        created_by_user_id=group.created_by_user_id,
        creator_name=group.creator.name if group.creator else "Unknown",
        member_count=len(members),
        created_at=group.created_at,
        members=members,
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.post("", response_model=GroupDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    data: GroupCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new group. Creator auto-joins as admin."""
    # Check if user is already in a group
    existing = await get_user_group_membership(db, current_user.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already in a group. Leave your current group first."
        )

    group = Group(
        name=data.name,
        description=data.description,
        created_by_user_id=current_user.id,
    )
    db.add(group)
    await db.flush()

    # Auto-join creator as admin
    membership = GroupMember(
        group_id=group.id,
        user_id=current_user.id,
        role="admin",
    )
    db.add(membership)
    await db.commit()

    # Reload with relationships
    result = await db.execute(
        select(Group)
        .options(
            selectinload(Group.creator),
            selectinload(Group.members).selectinload(GroupMember.user)
        )
        .where(Group.id == group.id)
    )
    group = result.scalar_one()

    return build_group_detail_response(group)


@router.get("", response_model=GroupListResponse)
async def list_groups(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all groups with member counts."""
    result = await db.execute(
        select(
            Group,
            func.count(GroupMember.id).label("member_count")
        )
        .options(selectinload(Group.creator))
        .outerjoin(GroupMember, Group.id == GroupMember.group_id)
        .group_by(Group.id)
        .order_by(Group.created_at.desc())
    )
    rows = result.unique().all()

    items = []
    for group, member_count in rows:
        items.append(build_group_response(group, member_count))

    return GroupListResponse(items=items, total=len(items))


@router.get("/my", response_model=MyGroupResponse)
async def get_my_group(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get current user's group, or null if not in any group."""
    membership = await get_user_group_membership(db, current_user.id)
    if not membership:
        return MyGroupResponse(group=None, role=None)

    result = await db.execute(
        select(Group)
        .options(
            selectinload(Group.creator),
            selectinload(Group.members).selectinload(GroupMember.user)
        )
        .where(Group.id == membership.group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        return MyGroupResponse(group=None, role=None)

    return MyGroupResponse(
        group=build_group_detail_response(group),
        role=membership.role,
    )


@router.get("/{group_id}", response_model=GroupDetailResponse)
async def get_group(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get group detail with members list."""
    result = await db.execute(
        select(Group)
        .options(
            selectinload(Group.creator),
            selectinload(Group.members).selectinload(GroupMember.user)
        )
        .where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found"
        )

    return build_group_detail_response(group)


@router.patch("/{group_id}", response_model=GroupDetailResponse)
async def update_group(
    group_id: int,
    data: GroupUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update group name/description (creator only)."""
    result = await db.execute(
        select(Group)
        .options(
            selectinload(Group.creator),
            selectinload(Group.members).selectinload(GroupMember.user)
        )
        .where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found"
        )

    if group.created_by_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the group creator can update the group"
        )

    if data.name is not None:
        group.name = data.name
    if data.description is not None:
        group.description = data.description

    await db.commit()
    await db.refresh(group)

    # Reload with relationships
    result = await db.execute(
        select(Group)
        .options(
            selectinload(Group.creator),
            selectinload(Group.members).selectinload(GroupMember.user)
        )
        .where(Group.id == group.id)
    )
    group = result.scalar_one()

    return build_group_detail_response(group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete group (creator only). Experiments stay but lose group_id."""
    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found"
        )

    if group.created_by_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the group creator can delete the group"
        )

    await db.delete(group)
    await db.commit()


@router.post("/{group_id}/join", response_model=GroupDetailResponse)
async def join_group(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Join a group. Error if already in a group."""
    # Check if user is already in a group
    existing = await get_user_group_membership(db, current_user.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already in a group. Leave your current group first."
        )

    # Verify group exists
    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found"
        )

    membership = GroupMember(
        group_id=group_id,
        user_id=current_user.id,
        role="member",
    )
    db.add(membership)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already in a group."
        )

    # Reload with relationships
    result = await db.execute(
        select(Group)
        .options(
            selectinload(Group.creator),
            selectinload(Group.members).selectinload(GroupMember.user)
        )
        .where(Group.id == group_id)
    )
    group = result.scalar_one()

    return build_group_detail_response(group)


@router.post("/{group_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_group(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Leave a group. Experiments stay in the group."""
    result = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == current_user.id
        )
    )
    membership = result.scalar_one_or_none()

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not a member of this group"
        )

    # Prevent creator from leaving (must delete the group instead)
    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()
    if group and group.created_by_user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Group creator cannot leave. Delete the group instead."
        )

    await db.delete(membership)
    await db.commit()


@router.delete("/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def kick_member(
    group_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Kick a member from the group (creator only)."""
    # Verify group and creator
    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found"
        )

    if group.created_by_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the group creator can kick members"
        )

    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot kick yourself. Use delete group instead."
        )

    # Find membership
    result = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id
        )
    )
    membership = result.scalar_one_or_none()

    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not a member of this group"
        )

    await db.delete(membership)
    await db.commit()
