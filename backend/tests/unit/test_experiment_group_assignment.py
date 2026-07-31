"""Sharing an experiment with a group is an explicit, owner-only act.

Joining a group used to re-stamp every group-less experiment the joiner owned
(``adopt_orphan_experiments``). That had exactly one right answer while a person
could belong to one group; with several it would either pick arbitrarily or, on
the second join, take work away from the first group. It was removed, and this
endpoint is what replaced it.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from routers import experiments as exp_router
from tests.unit.conftest import make_result


def _user(uid=7):
    return SimpleNamespace(id=uid)


async def test_missing_experiment_is_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as exc:
        await exp_router.update_experiment_group(
            experiment_id=3, group_id=2, current_user=_user(), db=mock_db
        )
    assert exc.value.status_code == 404


async def test_group_assignment_is_owner_only(mock_db):
    """A container belongs to whoever uploaded it -- the same rule that keeps
    deleting a FOV and renaming an experiment owner-only."""
    mock_db.execute.return_value = make_result(
        scalar=SimpleNamespace(id=3, user_id=99, group_id=None)
    )
    with pytest.raises(HTTPException) as exc:
        await exp_router.update_experiment_group(
            experiment_id=3, group_id=2, current_user=_user(7), db=mock_db
        )
    assert exc.value.status_code == 403


async def test_cannot_donate_an_experiment_to_a_group_you_are_not_in(mock_db):
    exp = SimpleNamespace(id=3, user_id=7, group_id=None)
    mock_db.execute.return_value = make_result(scalar=exp)
    with patch.object(exp_router, "get_user_group_ids", AsyncMock(return_value=[5])):
        with pytest.raises(HTTPException) as exc:
            await exp_router.update_experiment_group(
                experiment_id=3, group_id=2, current_user=_user(7), db=mock_db
            )
    assert exc.value.status_code == 400
    assert exp.group_id is None


async def test_sharing_with_one_of_my_groups_works(mock_db):
    exp = SimpleNamespace(id=3, user_id=7, group_id=None)
    mock_db.execute.return_value = make_result(scalar=exp)
    with patch.object(exp_router, "get_user_group_ids", AsyncMock(return_value=[2, 5])), \
         patch.object(exp_router, "load_experiment_response", AsyncMock()):
        await exp_router.update_experiment_group(
            experiment_id=3, group_id=5, current_user=_user(7), db=mock_db
        )
    assert exp.group_id == 5
    mock_db.commit.assert_awaited_once()


async def test_omitting_the_group_unshares(mock_db):
    exp = SimpleNamespace(id=3, user_id=7, group_id=2)
    mock_db.execute.return_value = make_result(scalar=exp)
    with patch.object(exp_router, "get_user_group_ids", AsyncMock()) as lookup, \
         patch.object(exp_router, "load_experiment_response", AsyncMock()):
        await exp_router.update_experiment_group(
            experiment_id=3, group_id=None, current_user=_user(7), db=mock_db
        )
    assert exp.group_id is None
    lookup.assert_not_awaited(), "unsharing needs no membership check"


async def test_the_response_is_rebuilt_by_reselecting_the_row(mock_db):
    """updated_at is server-generated and expires on commit; serializing the
    in-session object raises MissingGreenlet in production but not under
    AsyncMock, which is how that bug once reached users past a green suite."""
    exp = SimpleNamespace(id=3, user_id=7, group_id=None)
    mock_db.execute.return_value = make_result(scalar=exp)
    with patch.object(exp_router, "get_user_group_ids", AsyncMock(return_value=[2])), \
         patch.object(exp_router, "load_experiment_response", AsyncMock()) as loader:
        await exp_router.update_experiment_group(
            experiment_id=3, group_id=2, current_user=_user(7), db=mock_db
        )
    loader.assert_awaited_once()
    assert not mock_db.refresh.called, \
        "refresh(attribute_names=...) leaves updated_at expired -- reselect instead"
