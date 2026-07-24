"""UMAP microscope_id filter is wired into the endpoint + helpers."""
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.unit.conftest import make_result
from routers import embeddings as mod


def test_umap_endpoint_accepts_microscope_id():
    sig = inspect.signature(mod.get_umap_visualization)
    assert "microscope_id" in sig.parameters


async def test_umap_nonexistent_microscope_returns_404(mock_db):
    # A stale/deleted microscope id must fail loudly, not silently match 0 crops.
    mock_db.execute.return_value = make_result(scalar=None)
    with pytest.raises(HTTPException) as ei:
        await mod.get_umap_visualization(
            microscope_id=999, current_user=SimpleNamespace(id=1), db=mock_db
        )
    assert ei.value.status_code == 404


def test_cropped_helper_accepts_microscope_id():
    sig = inspect.signature(mod._get_cropped_umap)
    assert "microscope_id" in sig.parameters


def test_fov_helper_accepts_microscope_id():
    sig = inspect.signature(mod._get_fov_umap)
    assert "microscope_id" in sig.parameters
