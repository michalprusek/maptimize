"""Experiment ↔ microscope integration unit tests."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.unit.conftest import make_result
from routers import experiments as mod
from schemas.experiment import ExperimentCreate, ExperimentUpdate


def _user():
    return SimpleNamespace(id=1, name="Tester")


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
