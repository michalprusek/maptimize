"""Group routes.

Membership is many-to-many and joining is a request a group admin approves --
self-service join was removed, because it let anyone who could name a group id
into a lab's shared corpus.

Authorization for group administration is ``utils.groups.require_group_admin``:
the ``role`` column, plus a global ``users.role == ADMIN`` that counts as admin
everywhere. ``Group.created_by_user_id`` grants nothing; it is provenance.
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.user import User
from models.group import Group, GroupMember
from models.group_join_request import GroupJoinRequest, JoinRequestStatus
from schemas.group import (
    GroupCreate,
    GroupUpdate,
    GroupMemberResponse,
    GroupResponse,
    GroupDetailResponse,
    GroupListResponse,
    MyGroupMembership,
    MyGroupsResponse,
)
from schemas.group_join_request import (
    JoinRequestCreate,
    JoinRequestListResponse,
    JoinRequestResponse,
)
from utils.folder_seed import (
    detach_member_folder,
    dissolve_group_folders,
    ensure_group_folders,
    ensure_member_folder,
    rename_group_root_folder,
)
from utils.groups import require_group_admin
from utils.security import get_current_user, require_interactive_user

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Helper Functions
# =============================================================================


async def get_user_memberships(db: AsyncSession, user_id: int) -> List[GroupMember]:
    """Every membership the user holds. Empty list = ungrouped."""
    result = await db.execute(
        select(GroupMember).where(GroupMember.user_id == user_id)
    )
    return list(result.scalars().all())


async def load_group_detail(db: AsyncSession, group_id: int) -> Optional[Group]:
    """Re-select a group with its creator and members eagerly loaded.

    Always used to build a response after a write: serializing the in-session
    object instead would touch attributes the commit expired, and lazy IO in an
    async request raises MissingGreenlet rather than loading.

    Returns None for a missing group -- ``get_my_groups`` skips a membership that
    outlived its group rather than failing the whole listing. Endpoints addressing
    one group by id want :func:`load_group_detail_or_404`.
    """
    result = await db.execute(
        select(Group)
        .options(
            selectinload(Group.creator),
            selectinload(Group.members).selectinload(GroupMember.user),
        )
        .where(Group.id == group_id)
    )
    return result.scalar_one_or_none()


async def load_group_detail_or_404(db: AsyncSession, group_id: int) -> Group:
    """Same, but a missing group is a 404 rather than a None to check."""
    group = await load_group_detail(db, group_id)
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )
    return group


async def get_group_or_404(db: AsyncSession, group_id: int) -> Group:
    """The group row alone, for endpoints that never render its members."""
    group = (
        await db.execute(select(Group).where(Group.id == group_id))
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )
    return group


def build_join_request_response(
    req: GroupJoinRequest, group: Group, user: User
) -> JoinRequestResponse:
    return JoinRequestResponse(
        id=req.id,
        group_id=req.group_id,
        group_name=group.name if group else "Unknown",
        user_id=req.user_id,
        user_name=user.name if user else "Unknown",
        user_email=user.email if user else "",
        status=req.status,
        message=req.message,
        created_at=req.created_at,
        decided_at=req.decided_at,
        decided_by_user_id=req.decided_by_user_id,
    )


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
    """Create a new group. The creator joins it as its admin.

    Belonging to another group is not a reason to refuse: membership is
    many-to-many, and the lab group plus an institutional group is the case this
    exists for.
    """
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
    await ensure_group_folders(db, group)
    await ensure_member_folder(db, group, current_user)
    await db.commit()

    return build_group_detail_response(await load_group_detail(db, group.id))


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


@router.get("/my", response_model=MyGroupsResponse)
async def get_my_groups(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Every group the caller belongs to, with their role in each."""
    memberships = await get_user_memberships(db, current_user.id)
    items = []
    for membership in memberships:
        group = await load_group_detail(db, membership.group_id)
        if group is None:
            # Skipping beats a 500 -- their other groups still render. But
            # GroupMember.group_id is ON DELETE CASCADE, so this is supposed to
            # be unreachable; if it fires, a FK was bypassed and the user is
            # quietly missing a group from their settings page.
            logger.error(
                "Membership %s references missing group %s for user %s -- "
                "skipping; this should be impossible under the CASCADE FK",
                membership.id, membership.group_id, current_user.id,
            )
            continue
        items.append(MyGroupMembership(
            group=build_group_detail_response(group),
            role=membership.role,
        ))
    return MyGroupsResponse(items=items)


@router.get("/{group_id}", response_model=GroupDetailResponse)
async def get_group(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get group detail with members list."""
    return build_group_detail_response(await load_group_detail_or_404(db, group_id))


@router.patch("/{group_id}", response_model=GroupDetailResponse)
async def update_group(
    group_id: int,
    data: GroupUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update group name/description (group admin only)."""
    group = await load_group_detail_or_404(db, group_id)

    await require_group_admin(current_user.id, group_id, db, actor=current_user)

    if data.name is not None:
        group.name = data.name
        # The group's root folder is titled after it; letting the two drift would
        # leave members navigating a tree labelled with the old name.
        await rename_group_root_folder(db, group_id, data.name)
    if data.description is not None:
        group.description = data.description

    await db.commit()

    return build_group_detail_response(await load_group_detail(db, group_id))


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a group (group admin only).

    Experiments, documents and folders survive: their group_id is ON DELETE SET
    NULL, so they fall back to owner-only rather than disappearing with the group.
    """
    group = await get_group_or_404(db, group_id)

    await require_group_admin(current_user.id, group_id, db, actor=current_user)

    # Before the group goes: its seeded folders would otherwise survive with
    # kind still 'root'/'common'/'user' and become permanently un-editable, since
    # _reject_if_seeded protects a structure that no longer exists.
    await dissolve_group_folders(db, group_id)
    await db.delete(group)
    await db.commit()


# =============================================================================
# Join requests
#
# There is no self-service join: a request is created here and a group admin
# approves it. approve/reject additionally require an interactive login, so a
# connector token cannot admit its own user to a group -- and by the project's
# own rule, an endpoint behind require_interactive_user stays out of MCP.
# =============================================================================


async def _get_membership(db: AsyncSession, group_id: int, user_id: int) -> Optional[GroupMember]:
    result = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


@router.post(
    "/{group_id}/join-requests",
    response_model=JoinRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_join_request(
    group_id: int,
    body: JoinRequestCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ask to join a group. A group admin decides."""
    group = await get_group_or_404(db, group_id)

    if await _get_membership(db, group_id, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already a member of this group",
        )

    result = await db.execute(
        select(GroupJoinRequest).where(
            GroupJoinRequest.group_id == group_id,
            GroupJoinRequest.user_id == current_user.id,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        if existing.status == JoinRequestStatus.PENDING.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You already have a pending request for this group",
            )
        # A rejection is not permanent: reopen the same row rather than keeping a
        # dead one that would collide with the (group, user) unique constraint.
        existing.status = JoinRequestStatus.PENDING.value
        existing.message = body.message
        existing.decided_at = None
        existing.decided_by_user_id = None
        request = existing
    else:
        request = GroupJoinRequest(
            group_id=group_id,
            user_id=current_user.id,
            status=JoinRequestStatus.PENDING.value,
            message=body.message,
        )
        db.add(request)

    await db.commit()
    await db.refresh(request)
    return build_join_request_response(request, group, current_user)


@router.get("/join-requests/mine", response_model=JoinRequestListResponse)
async def list_my_join_requests(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The caller's own requests, in any state."""
    result = await db.execute(
        select(GroupJoinRequest, Group)
        .join(Group, Group.id == GroupJoinRequest.group_id)
        .where(GroupJoinRequest.user_id == current_user.id)
        .order_by(GroupJoinRequest.created_at.desc())
    )
    items = [
        build_join_request_response(req, group, current_user)
        for req, group in result.all()
    ]
    return JoinRequestListResponse(items=items, total=len(items))


@router.get("/{group_id}/join-requests", response_model=JoinRequestListResponse)
async def list_join_requests(
    group_id: int,
    request_status: str = JoinRequestStatus.PENDING.value,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The group's requests (group admin only). Defaults to the pending queue."""
    group = await get_group_or_404(db, group_id)
    await require_group_admin(current_user.id, group_id, db, actor=current_user)

    query = (
        select(GroupJoinRequest, User)
        .join(User, User.id == GroupJoinRequest.user_id)
        .where(GroupJoinRequest.group_id == group_id)
    )
    if request_status:
        query = query.where(GroupJoinRequest.status == request_status)

    result = await db.execute(query.order_by(GroupJoinRequest.created_at))
    items = [
        build_join_request_response(req, group, user) for req, user in result.all()
    ]
    return JoinRequestListResponse(items=items, total=len(items))


async def _load_pending_request(
    db: AsyncSession, group_id: int, request_id: int
) -> GroupJoinRequest:
    result = await db.execute(
        select(GroupJoinRequest).where(
            GroupJoinRequest.id == request_id,
            GroupJoinRequest.group_id == group_id,
            GroupJoinRequest.status == JoinRequestStatus.PENDING.value,
        )
    )
    request = result.scalar_one_or_none()
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending request with that id in this group",
        )
    return request


@router.post(
    "/{group_id}/join-requests/{request_id}/approve",
    response_model=GroupDetailResponse,
)
async def approve_join_request(
    group_id: int,
    request_id: int,
    current_user: User = Depends(require_interactive_user),
    db: AsyncSession = Depends(get_db),
):
    """Admit the requester (group admin, interactive login only).

    One transaction: the membership, the decision, and the member's private
    folder in this group. Seeding the folder later would leave a member whose
    tree has nowhere private to put anything.
    """
    await require_group_admin(current_user.id, group_id, db, actor=current_user)
    request = await _load_pending_request(db, group_id, request_id)

    if await _get_membership(db, group_id, request.user_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That user is already a member",
        )

    group = await get_group_or_404(db, group_id)
    result = await db.execute(select(User).where(User.id == request.user_id))
    requester = result.scalar_one_or_none()

    db.add(GroupMember(group_id=group_id, user_id=request.user_id, role="member"))
    request.status = JoinRequestStatus.APPROVED.value
    request.decided_at = datetime.now(timezone.utc)
    request.decided_by_user_id = current_user.id
    await ensure_member_folder(db, group, requester)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That user is already a member",
        )

    logger.info(
        f"User {request.user_id} admitted to group {group_id} by {current_user.id}"
    )
    return build_group_detail_response(await load_group_detail(db, group_id))


@router.post(
    "/{group_id}/join-requests/{request_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reject_join_request(
    group_id: int,
    request_id: int,
    current_user: User = Depends(require_interactive_user),
    db: AsyncSession = Depends(get_db),
):
    """Decline a request (group admin, interactive login only). The user may ask again."""
    await require_group_admin(current_user.id, group_id, db, actor=current_user)
    request = await _load_pending_request(db, group_id, request_id)

    request.status = JoinRequestStatus.REJECTED.value
    request.decided_at = datetime.now(timezone.utc)
    request.decided_by_user_id = current_user.id
    await db.commit()


@router.delete(
    "/{group_id}/join-requests/{request_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_join_request(
    group_id: int,
    request_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Withdraw your own PENDING request.

    The status filter is the point: without it a member could delete their own
    approved request and erase the record of who admitted them -- the provenance
    that ``decided_by_user_id``'s ON DELETE SET NULL exists to preserve even when
    the deciding admin's account is gone.
    """
    result = await db.execute(
        select(GroupJoinRequest).where(
            GroupJoinRequest.id == request_id,
            GroupJoinRequest.group_id == group_id,
            GroupJoinRequest.user_id == current_user.id,
            GroupJoinRequest.status == JoinRequestStatus.PENDING.value,
        )
    )
    request = result.scalar_one_or_none()
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending request of yours with that id",
        )
    await db.delete(request)
    await db.commit()


@router.post("/{group_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_group(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Leave one group. Experiments and shared documents stay behind."""
    membership = await _get_membership(db, group_id, current_user.id)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not a member of this group"
        )

    result = await db.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()

    if group and membership.role == "admin":
        # A group must never be left without an admin, or nobody can approve a
        # request or remove a member again.
        other_result = await db.execute(
            select(GroupMember)
            .where(
                GroupMember.group_id == group_id,
                GroupMember.user_id != current_user.id,
            )
            .order_by(GroupMember.joined_at, GroupMember.id)
        )
        others = list(other_result.scalars().all())
        remaining_admins = [m for m in others if m.role == "admin"]
        if not others:
            # Last member leaving -- the group has no reason to exist. This path
            # returns early, so it has to do its own folder cleanup: without it
            # the leaver keeps an un-editable root, `common` and private folder
            # belonging to a group that is gone.
            await dissolve_group_folders(db, group_id)
            await db.delete(membership)
            await db.delete(group)
            await db.commit()
            return
        if not remaining_admins:
            successor = others[0]
            successor.role = "admin"
            group.created_by_user_id = successor.user_id
            logger.info(
                f"Group {group_id}: promoted user {successor.user_id} to admin "
                f"as the last admin left"
            )

    await detach_member_folder(db, group_id, current_user.id)
    await db.delete(membership)
    await db.commit()


@router.delete("/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def kick_member(
    group_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Remove a member from the group (group admin only)."""
    await get_group_or_404(db, group_id)
    await require_group_admin(current_user.id, group_id, db, actor=current_user)

    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove yourself. Leave the group instead."
        )

    membership = await _get_membership(db, group_id, user_id)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not a member of this group"
        )

    await detach_member_folder(db, group_id, user_id)
    await db.delete(membership)
    await db.commit()
