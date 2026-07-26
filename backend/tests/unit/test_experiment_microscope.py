"""Experiment ↔ microscope integration unit tests."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from tests.unit.conftest import make_result
from routers import experiments as mod
from models.experiment import ExperimentStatus
from schemas.experiment import ExperimentCreate, ExperimentUpdate


def _user():
    return SimpleNamespace(id=1, name="Tester")


def _microscope(**kw):
    base = dict(id=5, name="Zeiss LSM 880", manufacturer="Zeiss", model=None,
                objective=None, magnification="63×", color="#3b82f6")
    base.update(kw)
    return SimpleNamespace(**base)


def test_microscope_is_assignable_at_creation_but_not_via_generic_patch():
    """One field, one endpoint, one ACL.

    `PATCH /experiments/{id}` is owner-only; the microscope endpoint is open to
    the whole group. If `microscope_id` were accepted by both, the narrow path
    would be reachable by accident and group members would silently get 403s.
    `extra="forbid"` makes a stale client fail loudly (422) instead of having its
    assignment quietly dropped.
    """
    assert "microscope_id" in ExperimentCreate.model_fields
    assert "microscope_id" not in ExperimentUpdate.model_fields
    with pytest.raises(ValidationError):
        ExperimentUpdate(microscope_id=5)


async def test_create_experiment_missing_microscope_404(mock_db, monkeypatch):
    async def fake_group_id(uid, db):
        return None
    monkeypatch.setattr(mod, "get_user_group_id", fake_group_id)
    # protein not requested; microscope lookup returns None → 404
    mock_db.execute.return_value = make_result(scalar=None)
    data = ExperimentCreate(name="E", microscope_id=42)
    with pytest.raises(HTTPException) as ei:
        await mod.create_experiment(data, current_user=_user(), db=mock_db)
    assert ei.value.status_code == 404
    assert "microscope" in ei.value.detail.lower()


def _experiment(owner_id: int = 1, microscope=None):
    return SimpleNamespace(
        id=1, user_id=owner_id, name="E", description=None,
        status=ExperimentStatus.DRAFT, group_id=2, map_protein=None,
        microscope=microscope, fasta_sequence=None,
        microscope_id=microscope.id if microscope else None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


async def test_create_experiment_with_valid_microscope(mock_db):
    # Microscope exists → experiment is created and the response embeds it.
    mock_db.execute.side_effect = [
        make_result(scalar=_microscope()),                          # existence check
        make_result(scalar=_experiment(microscope=_microscope())),   # response re-read
    ]
    with patch.object(mod, "get_user_group_id", new=AsyncMock(return_value=None)):
        out = await mod.create_experiment(
            ExperimentCreate(name="E", microscope_id=5), current_user=_user(), db=mock_db
        )
    assert out.microscope is not None
    assert out.microscope.id == 5 and out.microscope.magnification == "63×"


async def test_assign_microscope_missing_microscope_404(mock_db):
    mock_db.execute.return_value = make_result(scalar=None)  # microscope lookup → None
    with patch.object(mod, "get_experiment_for_user", new=AsyncMock(return_value=_experiment())):
        with pytest.raises(HTTPException) as ei:
            await mod.update_experiment_microscope(
                1, microscope_id=999, current_user=_user(), db=mock_db
            )
    assert ei.value.status_code == 404
    assert "microscope" in ei.value.detail.lower()


async def test_assign_microscope_with_valid_microscope(mock_db):
    exp = _experiment()
    mock_db.execute.side_effect = [
        make_result(scalar=_microscope()),                          # existence check
        make_result(scalar=_experiment(microscope=_microscope())),   # response re-read
    ]
    with patch.object(mod, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        out = await mod.update_experiment_microscope(
            1, microscope_id=5, current_user=_user(), db=mock_db
        )
    assert exp.microscope_id == 5
    assert out.microscope is not None and out.microscope.id == 5


async def test_group_member_may_assign_microscope_to_another_users_experiment(mock_db):
    """The reason this endpoint exists.

    31 of 37 experiments in production belong to the lab's annotator, so an
    owner-only assignment would leave the dashboard's microscope filter covering
    almost nothing. `get_experiment_for_user` is group-scoped and there is
    deliberately NO owner re-check after it -- unlike `update_experiment`.
    """
    exp = _experiment(owner_id=22)  # a colleague's experiment
    mock_db.execute.side_effect = [
        make_result(scalar=_microscope()),
        make_result(scalar=_experiment(owner_id=22, microscope=_microscope())),
    ]
    with patch.object(mod, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        out = await mod.update_experiment_microscope(
            1, microscope_id=5, current_user=_user(), db=mock_db
        )
    assert exp.microscope_id == 5
    assert out.microscope.id == 5


async def test_clearing_microscope_skips_the_existence_lookup(mock_db):
    """Omitting the id clears the assignment without looking a microscope up.

    Exactly one statement runs: the response re-read. A second one would mean the
    handler tried to validate `None` as a microscope id.
    """
    exp = _experiment(microscope=_microscope())
    mock_db.execute.side_effect = [make_result(scalar=_experiment())]

    with patch.object(mod, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        out = await mod.update_experiment_microscope(
            1, microscope_id=None, current_user=_user(), db=mock_db
        )
    assert exp.microscope_id is None
    assert out.microscope is None
    assert mock_db.execute.await_count == 1


def test_writes_rebuild_the_response_by_reselecting_the_row():
    """No experiment write may answer from the in-session object.

    `Experiment.updated_at` has `onupdate=func.now()`, so after an UPDATE the
    attribute is expired pending a re-read. Serialising it then attempts lazy IO
    and raises MissingGreenlet -- which really did make every rename return 500
    while the mocked unit tests stayed green, because an AsyncMock session has no
    expiry semantics. A mock can't reproduce that, so this asserts the *shape* of
    the fix instead: the three write handlers must go through
    `load_experiment_response` and must not hand-roll a partial `db.refresh`.
    """
    import inspect

    source = inspect.getsource(mod)
    for handler in ("create_experiment", "update_experiment", "update_experiment_microscope"):
        body = source.split(f"async def {handler}(", 1)[1].split("\n@router", 1)[0]
        assert "load_experiment_response" in body, (
            f"{handler} must build its response via load_experiment_response"
        )
        assert "attribute_names" not in body, (
            f"{handler} refreshes selected attributes again -- that is the "
            "MissingGreenlet trap on updated_at"
        )


async def test_generic_patch_stays_owner_only(mock_db):
    """Guard the boundary: widening one endpoint must not widen the other."""
    with patch.object(
        mod, "get_experiment_for_user", new=AsyncMock(return_value=_experiment(owner_id=22))
    ):
        with pytest.raises(HTTPException) as ei:
            await mod.update_experiment(
                1, ExperimentUpdate(name="renamed"), current_user=_user(), db=mock_db
            )
    assert ei.value.status_code == 403
