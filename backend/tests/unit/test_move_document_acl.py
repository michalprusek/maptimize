"""Moving a document is the one action that changes who can read it.

"Any group member may organize the group's documents" is the intended rule, and
it is right within a group. Multi-group membership made a second reading
reachable: a member of A and B can take a colleague's A-document and drop it into
B's `common`, disclosing it to a group its owner never joined and cannot see.
The owner is not notified, and nothing in their UI changes.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from routers import rag as rag_router


def _user(uid=7):
    return SimpleNamespace(id=uid)


def _doc(owner=7, group_id=2, folder_id=None):
    return SimpleNamespace(id=3, user_id=owner, group_id=group_id, folder_id=folder_id)


def _folder(fid=9, group_id=2, visibility="group"):
    return SimpleNamespace(id=fid, group_id=group_id, visibility=visibility)


async def _move(mock_db, document, folder, mover=7, owner_groups=None):
    with patch.object(rag_router, "get_user_group_ids",
                      AsyncMock(side_effect=[[2, 5], owner_groups or [2]])), \
         patch.object(rag_router, "get_document_for_user", AsyncMock(return_value=document)), \
         patch.object(rag_router, "get_target_folder", AsyncMock(return_value=folder)):
        return await rag_router.move_document(
            document_id=3,
            payload={"folder_id": folder.id if folder else None},
            current_user=_user(mover), db=mock_db,
        )


async def test_organising_within_the_group_is_allowed(mock_db):
    """The intended rule: a colleague may file a shared document."""
    doc = _doc(owner=8, group_id=2)
    out = await _move(mock_db, doc, _folder(group_id=2), mover=7, owner_groups=[2])
    assert out["group_id"] == 2


async def test_you_cannot_move_a_colleagues_document_into_a_group_they_are_not_in(mock_db):
    """Disclosure the owner never consented to and cannot see: they are not in B,
    so B's `common` does not appear anywhere in their tree."""
    doc = _doc(owner=8, group_id=2)
    with pytest.raises(HTTPException) as exc:
        await _move(mock_db, doc, _folder(fid=9, group_id=5), mover=7, owner_groups=[2])
    assert exc.value.status_code == 403
    assert doc.group_id == 2, "the document must not be re-stamped on refusal"


async def test_the_owner_may_move_their_own_document_between_their_groups(mock_db):
    """Their document, their groups -- nothing is disclosed to a stranger."""
    doc = _doc(owner=7, group_id=2)
    out = await _move(mock_db, doc, _folder(fid=9, group_id=5), mover=7, owner_groups=[2, 5])
    assert out["group_id"] == 5


async def test_moving_into_a_private_folder_unshares(mock_db):
    doc = _doc(owner=7, group_id=2)
    out = await _move(mock_db, doc, _folder(group_id=2, visibility="private"), mover=7)
    assert out["group_id"] is None


async def test_moving_to_the_root_unshares(mock_db):
    doc = _doc(owner=7, group_id=2, folder_id=9)
    with patch.object(rag_router, "get_user_group_ids", AsyncMock(return_value=[2])), \
         patch.object(rag_router, "get_document_for_user", AsyncMock(return_value=doc)):
        out = await rag_router.move_document(
            document_id=3, payload={"folder_id": None},
            current_user=_user(7), db=mock_db,
        )
    assert out["folder_id"] is None and out["group_id"] is None
