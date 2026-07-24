"""UMAP microscope_id filter is wired into the endpoint + helpers."""
import inspect

from routers import embeddings as mod


def test_umap_endpoint_accepts_microscope_id():
    sig = inspect.signature(mod.get_umap_visualization)
    assert "microscope_id" in sig.parameters


def test_cropped_helper_accepts_microscope_id():
    sig = inspect.signature(mod._get_cropped_umap)
    assert "microscope_id" in sig.parameters


def test_fov_helper_accepts_microscope_id():
    sig = inspect.signature(mod._get_fov_umap)
    assert "microscope_id" in sig.parameters
