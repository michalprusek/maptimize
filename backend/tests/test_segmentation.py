"""Tests for segmentation endpoints.

Integration tests that verify:
- GET /api/segmentation/capabilities - SAM capabilities info
- POST /api/segmentation/compute-embedding/{image_id} - trigger SAM embedding
- GET /api/segmentation/embedding-status/{image_id} - check embedding status
- POST /api/segmentation/segment - interactive point segmentation
- GET /api/segmentation/mask/{crop_id} - get crop mask
- GET /api/segmentation/masks/batch - batch get masks
- DELETE /api/segmentation/mask/{crop_id} - delete mask
- GET /api/segmentation/fov-mask/{image_id} - get FOV-level mask
- POST /api/segmentation/segment-text - text-based segmentation

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d
"""
import pytest


class TestSegmentationCapabilities:
    """Test suite for GET /api/segmentation/capabilities."""

    def test_capabilities_requires_auth(self, client):
        """Capabilities endpoint requires authentication."""
        response = client.get("/api/segmentation/capabilities")
        assert response.status_code == 401

    def test_capabilities_returns_expected_fields(self, client, auth_headers):
        """Capabilities returns device, variant, supports_text_prompts, model_name."""
        response = client.get(
            "/api/segmentation/capabilities",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "device" in data
        assert data["device"] in ["cuda", "mps", "cpu"]
        assert "variant" in data
        assert "supports_text_prompts" in data
        assert isinstance(data["supports_text_prompts"], bool)
        assert "model_name" in data
        assert isinstance(data["model_name"], str)


class TestSegmentationEmbedding:
    """Test suite for SAM embedding endpoints."""

    def test_compute_embedding_requires_auth(self, client):
        """Compute embedding without auth returns 401."""
        response = client.post("/api/segmentation/compute-embedding/1")
        assert response.status_code == 401

    def test_compute_embedding_nonexistent_image(self, client, auth_headers):
        """Compute embedding for nonexistent image returns 404."""
        response = client.post(
            "/api/segmentation/compute-embedding/999999",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_embedding_status_requires_auth(self, client):
        """Embedding status without auth returns 401."""
        response = client.get("/api/segmentation/embedding-status/1")
        assert response.status_code == 401

    def test_embedding_status_nonexistent_image(self, client, auth_headers):
        """Embedding status for nonexistent image returns 404."""
        response = client.get(
            "/api/segmentation/embedding-status/999999",
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestSegmentationMasks:
    """Test suite for mask CRUD endpoints."""

    def test_get_mask_requires_auth(self, client):
        """Get mask without auth returns 401."""
        response = client.get("/api/segmentation/mask/1")
        assert response.status_code == 401

    def test_get_mask_nonexistent_crop(self, client, auth_headers):
        """Get mask for nonexistent crop returns has_mask=false."""
        response = client.get(
            "/api/segmentation/mask/999999",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["has_mask"] is False

    def test_batch_masks_requires_auth(self, client):
        """Batch masks without auth returns 401."""
        response = client.get("/api/segmentation/masks/batch?crop_ids=1,2,3")
        assert response.status_code == 401

    def test_batch_masks_too_many_ids(self, client, auth_headers):
        """Batch masks with more than 100 IDs returns 400."""
        ids = ",".join(str(i) for i in range(1, 102))  # 101 IDs
        response = client.get(
            f"/api/segmentation/masks/batch?crop_ids={ids}",
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "100" in response.json()["detail"]

    def test_batch_masks_empty_returns_empty(self, client, auth_headers):
        """Batch masks with empty string returns empty masks dict."""
        response = client.get(
            "/api/segmentation/masks/batch?crop_ids=",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["masks"] == {}

    def test_batch_masks_invalid_format(self, client, auth_headers):
        """Batch masks with non-integer IDs returns 400."""
        response = client.get(
            "/api/segmentation/masks/batch?crop_ids=abc,def",
            headers=auth_headers,
        )
        assert response.status_code == 400

    def test_fov_mask_requires_auth(self, client):
        """FOV mask without auth returns 401."""
        response = client.get("/api/segmentation/fov-mask/1")
        assert response.status_code == 401

    def test_fov_mask_nonexistent_image(self, client, auth_headers):
        """FOV mask for nonexistent image returns has_mask=false."""
        response = client.get(
            "/api/segmentation/fov-mask/999999",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["has_mask"] is False


class TestSegmentationText:
    """Test suite for text-based segmentation."""

    def test_segment_text_requires_auth(self, client):
        """Text segmentation without auth returns 401."""
        response = client.post(
            "/api/segmentation/segment-text",
            json={"image_id": 1, "text_prompt": "cell"},
        )
        assert response.status_code == 401

    def test_segment_text_empty_prompt(self, client, auth_headers):
        """Text segmentation with empty prompt returns 422."""
        response = client.post(
            "/api/segmentation/segment-text",
            headers=auth_headers,
            json={"image_id": 1, "text_prompt": ""},
        )
        assert response.status_code == 422
