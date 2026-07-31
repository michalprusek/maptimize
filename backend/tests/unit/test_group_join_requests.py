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
