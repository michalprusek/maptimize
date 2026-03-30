"""Tests for embedding and UMAP visualization endpoints.

Integration tests that verify:
- GET /api/embeddings/status - feature extraction status
- GET /api/embeddings/umap - UMAP visualization coordinates
- POST /api/embeddings/umap/recompute - trigger UMAP recomputation
- POST /api/embeddings/extract - trigger crop feature extraction
- POST /api/embeddings/extract-fov - trigger FOV feature extraction

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

    def test_umap_recompute_requires_auth(self, client):
        """UMAP recompute without auth returns 401."""
        response = client.post("/api/embeddings/umap/recompute?umap_type=cropped")
        assert response.status_code == 401

    def test_umap_recompute_valid(self, client, auth_headers):
        """UMAP recompute with valid type returns success message."""
        response = client.post(
            "/api/embeddings/umap/recompute?umap_type=cropped",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "cropped" in data["message"].lower()


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
