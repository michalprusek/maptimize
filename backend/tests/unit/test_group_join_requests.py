"""Multi-group membership and the join-request flow.

Two things are locked here. First the *shape*: a user may hold one membership per
group rather than one membership full stop, so the old ``uq_user_one_group`` must
be gone and not quietly reintroduced. Second the *authorization*: joining is a
request a group admin approves, and self-service join -- which used to admit
anyone who could name a group id -- must stay deleted.
"""
from models.group import GroupMember
from models.group_join_request import GroupJoinRequest, JoinRequestStatus
from models.document_folder import (
    DocumentFolder,
    FOLDER_KIND_COMMON,
    FOLDER_KIND_CUSTOM,
    FOLDER_KIND_ROOT,
    FOLDER_KIND_USER,
    FOLDER_VISIBILITY_GROUP,
    FOLDER_VISIBILITY_PRIVATE,
)


def test_group_membership_is_unique_per_group_not_per_user():
    """A user may hold one membership in each of several groups."""
    names = {c.name for c in GroupMember.__table__.constraints if c.name}
    assert "uq_user_one_group" not in names, "the one-group-per-user constraint must be gone"
    uq = next(c for c in GroupMember.__table__.constraints if c.name == "uq_group_member")
    assert {col.name for col in uq.columns} == {"group_id", "user_id"}


def test_join_request_table_shape():
    cols = GroupJoinRequest.__table__.columns
    assert {
        "id", "group_id", "user_id", "status", "message",
        "created_at", "decided_at", "decided_by_user_id",
    } <= set(cols.keys())
    uq = next(
        c for c in GroupJoinRequest.__table__.constraints
        if getattr(c, "name", None) == "uq_join_request_group_user"
    )
    assert {col.name for col in uq.columns} == {"group_id", "user_id"}
    assert JoinRequestStatus.PENDING.value == "pending"
    assert JoinRequestStatus.APPROVED.value == "approved"
    assert JoinRequestStatus.REJECTED.value == "rejected"


def test_folder_carries_visibility_and_kind():
    cols = DocumentFolder.__table__.columns
    assert cols["visibility"].default.arg == FOLDER_VISIBILITY_GROUP
    assert cols["kind"].default.arg == FOLDER_KIND_CUSTOM
    assert {FOLDER_VISIBILITY_GROUP, FOLDER_VISIBILITY_PRIVATE} == {"group", "private"}
    assert {FOLDER_KIND_ROOT, FOLDER_KIND_COMMON, FOLDER_KIND_USER} == {"root", "common", "user"}


# =============================================================================
# The approval flow
# =============================================================================

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from models.user import UserRole
from routers import groups as groups_router
from tests.unit.conftest import make_result


def _user(uid, role=UserRole.RESEARCHER, name=None):
    return SimpleNamespace(
        id=uid, email=f"u{uid}@x.cz", name=name or f"U{uid}", role=role
    )


def _group(gid=2, name="Dr. Janke Lab", creator=2):
    return SimpleNamespace(
        id=gid, name=name, description=None, created_by_user_id=creator,
        creator=_user(creator), members=[], created_at=datetime(2026, 1, 1),
    )


def _request_row(rid=9, gid=2, uid=7, status="pending"):
    return SimpleNamespace(
        id=rid, group_id=gid, user_id=uid, status=status, message=None,
        created_at=datetime(2026, 7, 31), decided_at=None, decided_by_user_id=None,
    )


def test_open_self_join_is_gone():
    """Self-service join let anyone who could name a group id into a lab's
    shared corpus. It is the hole this feature closes."""
    assert not hasattr(groups_router, "join_group")


async def test_a_plain_member_cannot_approve(mock_db):
    mock_db.execute.return_value = make_result(scalar="member")
    with pytest.raises(HTTPException) as exc:
        await groups_router.approve_join_request(
            group_id=2, request_id=9, current_user=_user(7), db=mock_db
        )
    assert exc.value.status_code == 403


async def test_a_global_admin_is_admin_of_every_group(mock_db):
    """No per-group row needed -- which is what makes it durable for groups that
    do not exist yet."""
    from utils.groups import require_group_admin

    mock_db.execute.return_value = make_result(scalar=None)  # no membership at all
    await require_group_admin(1, 2, mock_db, actor=_user(1, UserRole.ADMIN))


async def test_approval_creates_the_membership_and_the_private_folder(mock_db):
    request = _request_row()
    group = _group()
    mock_db.execute.side_effect = [
        make_result(scalar="admin"),      # require_group_admin
        make_result(scalar=request),      # the pending request
        make_result(scalar=None),         # not already a member
        make_result(scalar=group),        # the group
        make_result(scalar=_user(7)),     # the requester
        make_result(scalar=group),        # load_group_detail
    ]
    with patch.object(groups_router, "ensure_member_folder", AsyncMock()) as seed:
        await groups_router.approve_join_request(
            group_id=2, request_id=9, current_user=_user(1), db=mock_db
        )

    assert request.status == "approved"
    assert request.decided_by_user_id == 1
    assert request.decided_at is not None
    added = [c.args[0] for c in mock_db.add.call_args_list]
    assert any(getattr(o, "role", None) == "member" for o in added), \
        "approval must create the membership"
    seed.assert_awaited_once()


async def test_requesting_twice_is_a_conflict(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=_group()),          # group exists
        make_result(scalar=None),              # not a member
        make_result(scalar=_request_row()),    # already pending
    ]
    with pytest.raises(HTTPException) as exc:
        await groups_router.create_join_request(
            group_id=2, body=SimpleNamespace(message=None),
            current_user=_user(7), db=mock_db,
        )
    assert exc.value.status_code == 409


async def test_a_rejection_can_be_appealed(mock_db):
    """A rejected row is reopened rather than left to collide with the
    (group, user) unique constraint on the next attempt."""
    rejected = _request_row(status="rejected")
    rejected.decided_at = datetime(2026, 7, 1)
    rejected.decided_by_user_id = 1
    mock_db.execute.side_effect = [
        make_result(scalar=_group()),
        make_result(scalar=None),
        make_result(scalar=rejected),
    ]
    await groups_router.create_join_request(
        group_id=2, body=SimpleNamespace(message="please"),
        current_user=_user(7), db=mock_db,
    )
    assert rejected.status == "pending"
    assert rejected.decided_at is None and rejected.decided_by_user_id is None
    mock_db.add.assert_not_called()


async def test_members_cannot_request_to_join_again(mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar=_group()),
        make_result(scalar=SimpleNamespace(id=1, role="member")),
    ]
    with pytest.raises(HTTPException) as exc:
        await groups_router.create_join_request(
            group_id=2, body=SimpleNamespace(message=None),
            current_user=_user(7), db=mock_db,
        )
    assert exc.value.status_code == 409


async def test_approve_and_reject_require_an_interactive_login():
    """Membership is the security boundary: a connector token must not be able to
    admit its own user. That also keeps both endpoints out of MCP by the
    project's existing rule."""
    import inspect

    from utils.security import require_interactive_user

    for handler in (groups_router.approve_join_request, groups_router.reject_join_request):
        dep = inspect.signature(handler).parameters["current_user"].default
        assert dep.dependency is require_interactive_user, handler.__name__


# =============================================================================
# Folder seeding (utils/folder_seed.py)
# =============================================================================

from models.document_folder import (
    FOLDER_VISIBILITY_PRIVATE as PRIVATE,
    FOLDER_KIND_USER as KIND_USER,
    FOLDER_KIND_ROOT as KIND_ROOT,
    FOLDER_KIND_CUSTOM as KIND_CUSTOM,
)
from utils import folder_seed


async def test_seeding_twice_creates_nothing_the_second_time(mock_db):
    """The production backfill is re-runnable, so seeding must be idempotent."""
    root = DocumentFolder(id=1, name="G", kind=KIND_ROOT, group_id=2)
    common = DocumentFolder(id=2, name="common", kind=FOLDER_KIND_COMMON, group_id=2)
    mock_db.execute.side_effect = [
        make_result(scalars_all=[root]),
        make_result(scalars_all=[common]),
    ]
    got_root, got_common = await folder_seed.ensure_group_folders(mock_db, _group())
    assert (got_root, got_common) == (root, common)
    mock_db.add.assert_not_called()


async def test_a_member_folder_is_private_and_hangs_under_the_group_root(mock_db):
    root = DocumentFolder(id=1, name="G", kind=KIND_ROOT, group_id=2)
    mock_db.execute.side_effect = [
        make_result(scalars_all=[]),      # no existing member folder
        make_result(scalars_all=[root]),  # root exists
        make_result(scalars_all=[DocumentFolder(id=2, kind=FOLDER_KIND_COMMON)]),
    ]
    folder = await folder_seed.ensure_member_folder(mock_db, _group(), _user(7, name="Theo"))
    assert folder.visibility == PRIVATE
    assert folder.kind == KIND_USER
    assert folder.user_id == 7
    assert folder.parent_id == 1
    assert folder.name == "Theo"


async def test_cancelling_only_reaches_a_pending_request(mock_db):
    """Without the status filter a member could delete their own APPROVED request
    and erase who admitted them -- the provenance decided_by_user_id's ON DELETE
    SET NULL exists to keep even when that admin's account is gone."""
    import inspect

    src = inspect.getsource(groups_router.cancel_join_request)
    assert "GroupJoinRequest.status == JoinRequestStatus.PENDING.value" in src, \
        "cancel deletes by (id, group, user) with no status filter"


async def test_disbanding_a_group_does_not_leave_undeletable_folders(mock_db):
    """group_id is ON DELETE SET NULL, so the seeded folders survive the group.
    Left with kind='root'/'common'/'user' they hit _reject_if_seeded forever,
    protecting a structure that no longer exists."""
    from models.document_folder import FOLDER_KIND_COMMON, FOLDER_KIND_ROOT

    root = DocumentFolder(id=1, name="G", kind=FOLDER_KIND_ROOT, group_id=2,
                          visibility=FOLDER_VISIBILITY_GROUP)
    common = DocumentFolder(id=2, name="common", kind=FOLDER_KIND_COMMON, group_id=2,
                            parent_id=1, visibility=FOLDER_VISIBILITY_GROUP)
    mine = DocumentFolder(id=3, name="Theo", kind=KIND_USER, group_id=2,
                          parent_id=1, visibility=PRIVATE)
    mock_db.execute.return_value = make_result(scalars_all=[root, common, mine])

    await folder_seed.dissolve_group_folders(mock_db, group_id=2)

    for folder in (root, common, mine):
        assert folder.kind == KIND_CUSTOM, f"{folder.name} stayed seeded"
        assert folder.group_id is None
        assert folder.parent_id is None
        assert folder.visibility == PRIVATE


async def test_leaving_cuts_the_private_folder_loose(mock_db):
    """Left attached, it hangs under a root the ex-member can no longer see --
    so it disappears from their tree while still holding their files."""
    folder = DocumentFolder(
        id=5, user_id=7, group_id=2, parent_id=1, name="Theo",
        kind=KIND_USER, visibility=PRIVATE,
    )
    mock_db.execute.return_value = make_result(scalars_all=[folder])
    await folder_seed.detach_member_folder(mock_db, group_id=2, user_id=7)
    assert folder.parent_id is None
    assert folder.group_id is None
    assert folder.kind == KIND_CUSTOM
    assert folder.visibility == PRIVATE
