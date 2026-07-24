"""Experiment ↔ microscope integration unit tests."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

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


def test_schemas_have_microscope_id():
    assert "microscope_id" in ExperimentCreate.model_fields
    assert "microscope_id" in ExperimentUpdate.model_fields


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


async def test_create_experiment_with_valid_microscope(mock_db):
    # Microscope exists → experiment is created and the response embeds it.
    mock_db.execute.return_value = make_result(scalar=_microscope())  # existence check

    async def _refresh(obj, *a, **k):
        obj.id = 50
        obj.status = ExperimentStatus.DRAFT
        obj.map_protein = None
        obj.microscope = _microscope()
        obj.group_id = None
        obj.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        obj.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)

    mock_db.refresh.side_effect = _refresh
    with patch.object(mod, "get_user_group_id", new=AsyncMock(return_value=None)):
        out = await mod.create_experiment(
            ExperimentCreate(name="E", microscope_id=5), current_user=_user(), db=mock_db
        )
    assert out.microscope is not None
    assert out.microscope.id == 5 and out.microscope.magnification == "63×"


async def test_update_experiment_missing_microscope_404(mock_db):
    exp = SimpleNamespace(id=1, user_id=1)
    mock_db.execute.return_value = make_result(scalar=None)  # microscope lookup → None
    with patch.object(mod, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        with pytest.raises(HTTPException) as ei:
            await mod.update_experiment(
                1, ExperimentUpdate(microscope_id=999), current_user=_user(), db=mock_db
            )
    assert ei.value.status_code == 404
    assert "microscope" in ei.value.detail.lower()


async def test_update_experiment_with_valid_microscope(mock_db):
    exp = SimpleNamespace(
        id=1, user_id=1, name="E", description=None, status=ExperimentStatus.DRAFT,
        group_id=None, map_protein=None, microscope=None, fasta_sequence=None,
        microscope_id=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    mock_db.execute.return_value = make_result(scalar=_microscope())  # existence check

    async def _refresh(obj, *a, **k):
        obj.microscope = _microscope()

    mock_db.refresh.side_effect = _refresh
    with patch.object(mod, "get_experiment_for_user", new=AsyncMock(return_value=exp)):
        out = await mod.update_experiment(
            1, ExperimentUpdate(microscope_id=5), current_user=_user(), db=mock_db
        )
    assert exp.microscope_id == 5
    assert out.microscope is not None and out.microscope.id == 5
