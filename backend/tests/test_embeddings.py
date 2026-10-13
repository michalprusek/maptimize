"""Tests for embedding and UMAP visualization endpoints.

Integration tests that verify:
- GET /api/embeddings/status - feature extraction status
- GET /api/embeddings/umap - UMAP visualization coordinates
- POST /api/embeddings/umap/recompute - trigger UMAP recomputation
- POST /api/embeddings/extract - trigger crop feature extraction

The FOV-level projection and POST /api/embeddings/extract-fov were removed on
2026-10-13; a client still asking for ``umap_type=fov`` gets 410.

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d
"""
import pytest


class TestEmbeddingStatus:
    """Test suite for GET /api/embeddings/status."""

    def test_status_requires_auth(self, client):
        """Embedding status without auth returns 401."""
        response = client.get("/api/embeddings/status")
        assert response.status_code == 401

    def test_status_returns_expected_fields(self, client, auth_headers):
        """Embedding status returns total, with_embeddings, without_embeddings, percentage."""
        response = client.get("/api/embeddings/status", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "with_embeddings" in data
        assert "without_embeddings" in data
        assert "percentage" in data
        assert isinstance(data["total"], int)
        assert isinstance(data["percentage"], (int, float))
        assert data["total"] >= 0
        assert data["with_embeddings"] >= 0
        assert data["without_embeddings"] >= 0
        assert 0 <= data["percentage"] <= 100

    def test_status_with_experiment_id(self, client, auth_headers):
        """Embedding status can be filtered by experiment_id."""
        experiments = client.get("/api/experiments/", headers=auth_headers)
        if experiments.status_code != 200 or len(experiments.json()) == 0:
            pytest.skip("No experiments available for testing")

        experiment_id = experiments.json()[0]["id"]
        response = client.get(
            f"/api/embeddings/status?experiment_id={experiment_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == data["with_embeddings"] + data["without_embeddings"]


class TestUMAP:
    """Test suite for UMAP visualization endpoints."""

    def test_umap_requires_auth(self, client):
        """UMAP endpoint without auth returns 401."""
        response = client.get("/api/embeddings/umap")
        assert response.status_code == 401

    def test_umap_nonexistent_experiment(self, client, auth_headers):
        """UMAP for nonexistent experiment returns 404."""
        response = client.get(
            "/api/embeddings/umap?experiment_id=999999",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_umap_nonexistent_microscope(self, client, auth_headers):
        """UMAP filtered by a nonexistent microscope returns 404 (not a misleading
        'not enough crops'). Microscopes are shared data anyone can delete, so a
        stale id must fail loudly and specifically."""
        response = client.get(
            "/api/embeddings/umap?microscope_id=999999",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_umap_recompute_requires_auth(self, client):
        """UMAP recompute without auth returns 401."""
        response = client.post("/api/embeddings/umap/recompute")
        assert response.status_code == 401

    def test_umap_recompute_valid(self, client, auth_headers):
        """UMAP recompute takes no projection type and returns a success message."""
        response = client.post(
            "/api/embeddings/umap/recompute",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json() == {"message": "UMAP recomputation started"}

    @pytest.mark.parametrize("path", ["/api/embeddings/umap", "/api/embeddings/separability"])
    def test_removed_fov_projection_is_gone_not_crop_data(self, client, auth_headers, path):
        """A tab loaded before the FOV projection was removed still asks for it.

        Answering with crop points would draw them under an "FOV" heading; 410
        carries a sentence the cached bundle displays instead.
        """
        response = client.get(f"{path}?umap_type=fov", headers=auth_headers)
        assert response.status_code == 410
        assert "FOV projection was removed" in response.json()["detail"]


class TestFeatureExtraction:
    """Test suite for feature extraction trigger endpoints."""

    def test_extract_requires_auth(self, client):
        """Feature extraction without auth returns 401."""
        response = client.post("/api/embeddings/extract?experiment_id=1")
        assert response.status_code == 401

    def test_extract_requires_experiment_id(self, client, auth_headers):
        """Feature extraction without experiment_id returns 422."""
        response = client.post(
            "/api/embeddings/extract",
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_extract_nonexistent_experiment(self, client, auth_headers):
        """Feature extraction for nonexistent experiment returns 404."""
        response = client.post(
            "/api/embeddings/extract?experiment_id=999999",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_extract_fov_endpoint_is_removed(self, client, auth_headers):
        """FOV embeddings are no longer computed, so the trigger no longer exists."""
        response = client.post("/api/embeddings/extract-fov", headers=auth_headers)
        assert response.status_code == 404
