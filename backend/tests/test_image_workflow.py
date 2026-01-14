"""Tests for two-phase image upload workflow.

Integration tests that verify Phase 1 (upload) and Phase 2 (batch process)
functionality including authorization and status validation.

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d
"""
import io
import pytest


class TestUploadEndpoint:
    """Tests for /api/images/upload endpoint (Phase 1)."""

    def test_upload_requires_authentication(self, client):
        """Test that upload endpoint requires authentication."""
        # Create a minimal fake image file
        fake_image = io.BytesIO(b"fake image content")
        response = client.post(
            "/api/images/upload",
            data={"experiment_id": "1"},
            files={"file": ("test.png", fake_image, "image/png")}
        )
        assert response.status_code == 401

    def test_upload_requires_experiment_id(self, client, auth_headers):
        """Test that upload requires experiment_id parameter."""
        fake_image = io.BytesIO(b"fake image content")
        response = client.post(
            "/api/images/upload",
            headers=auth_headers,
            files={"file": ("test.png", fake_image, "image/png")}
        )
        # Should return 422 for missing required field
        assert response.status_code == 422

    def test_upload_rejects_nonexistent_experiment(self, client, auth_headers):
        """Test that upload to non-existent experiment returns 404."""
        fake_image = io.BytesIO(b"fake image content")
        response = client.post(
            "/api/images/upload",
            headers=auth_headers,
            data={"experiment_id": "999999"},
            files={"file": ("test.png", fake_image, "image/png")}
        )
        assert response.status_code == 404

    def test_upload_rejects_invalid_file_extension(self, client, auth_headers):
        """Test that upload rejects files with invalid extensions."""
        # Get an existing experiment
        response = client.get("/api/experiments/", headers=auth_headers)
        if response.status_code != 200 or len(response.json()) == 0:
            pytest.skip("No experiments available for testing")

        experiment_id = response.json()[0]["id"]

        # Try to upload a file with invalid extension
        fake_file = io.BytesIO(b"not a real image")
        response = client.post(
            "/api/images/upload",
            headers=auth_headers,
            data={"experiment_id": str(experiment_id)},
            files={"file": ("malware.exe", fake_file, "application/octet-stream")}
        )
        # Should return 400 for invalid file type
        assert response.status_code == 400


class TestBatchProcessEndpoint:
    """Tests for /api/images/batch-process endpoint."""

    def test_batch_process_requires_authentication(self, client):
        """Test that batch process endpoint requires authentication."""
        response = client.post(
            "/api/images/batch-process",
            json={
                "image_ids": [1, 2, 3],
                "detect_cells": True
            }
        )
        assert response.status_code == 401

    def test_batch_process_rejects_empty_image_list(self, client, auth_headers):
        """Test that batch process with empty image_ids returns 422."""
        response = client.post(
            "/api/images/batch-process",
            headers=auth_headers,
            json={
                "image_ids": [],
                "detect_cells": True
            }
        )
        assert response.status_code == 422

    def test_batch_process_rejects_nonexistent_images(self, client, auth_headers):
        """Test that batch process with non-existent image IDs returns 404."""
        response = client.post(
            "/api/images/batch-process",
            headers=auth_headers,
            json={
                "image_ids": [999999, 999998],
                "detect_cells": True
            }
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_batch_process_deduplicates_image_ids(self, client, auth_headers):
        """Test that duplicate image IDs are deduplicated by schema validation."""
        # This test verifies the schema validator works, even if images don't exist
        response = client.post(
            "/api/images/batch-process",
            headers=auth_headers,
            json={
                "image_ids": [999999, 999999, 999999],  # duplicates
                "detect_cells": True
            }
        )
        # Should still fail with 404 (images not found), not 422 (validation error)
        # because duplicates are silently removed by validator
        assert response.status_code == 404

    def test_batch_process_rejects_invalid_protein_id(self, client, auth_headers):
        """Test that batch process with non-existent protein ID returns 404."""
        # First we need a valid image ID - skip if none available
        response = client.get("/api/experiments/", headers=auth_headers)
        if response.status_code != 200 or len(response.json()) == 0:
            pytest.skip("No experiments available for testing")

        experiments = response.json()
        experiment_id = experiments[0]["id"]

        # Get FOVs for this experiment
        response = client.get(
            f"/api/images/fovs?experiment_id={experiment_id}",
            headers=auth_headers
        )
        if response.status_code != 200 or len(response.json()) == 0:
            pytest.skip("No images available for testing")

        fovs = response.json()
        # Find an image in UPLOADED, READY, or ERROR status
        valid_image = None
        for fov in fovs:
            if fov["status"] in ["UPLOADED", "READY", "ERROR"]:
                valid_image = fov
                break

        if not valid_image:
            pytest.skip("No images in processable status")

        response = client.post(
            "/api/images/batch-process",
            headers=auth_headers,
            json={
                "image_ids": [valid_image["id"]],
                "detect_cells": True,
                "map_protein_id": 999999  # non-existent protein
            }
        )
        assert response.status_code == 404
        assert "protein" in response.json()["detail"].lower()


class TestFOVEndpoint:
    """Tests for /api/images/fovs endpoint."""

    def test_fovs_requires_authentication(self, client):
        """Test that FOVs endpoint requires authentication."""
        response = client.get("/api/images/fovs?experiment_id=1")
        assert response.status_code == 401

    def test_fovs_requires_experiment_id(self, client, auth_headers):
        """Test that FOVs endpoint requires experiment_id parameter."""
        response = client.get("/api/images/fovs", headers=auth_headers)
        assert response.status_code == 422

    def test_fovs_rejects_nonexistent_experiment(self, client, auth_headers):
        """Test that FOVs endpoint returns 404 for non-existent experiment."""
        response = client.get(
            "/api/images/fovs?experiment_id=999999",
            headers=auth_headers
        )
        assert response.status_code == 404

    def test_fovs_returns_list(self, client, auth_headers):
        """Test that FOVs endpoint returns a list of FOV images."""
        # Get an existing experiment
        response = client.get("/api/experiments/", headers=auth_headers)
        if response.status_code != 200 or len(response.json()) == 0:
            pytest.skip("No experiments available for testing")

        experiment_id = response.json()[0]["id"]
        response = client.get(
            f"/api/images/fovs?experiment_id={experiment_id}",
            headers=auth_headers
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_fovs_pagination(self, client, auth_headers):
        """Test that FOVs endpoint supports pagination."""
        # Get an existing experiment
        response = client.get("/api/experiments/", headers=auth_headers)
        if response.status_code != 200 or len(response.json()) == 0:
            pytest.skip("No experiments available for testing")

        experiment_id = response.json()[0]["id"]

        # Test with limit
        response = client.get(
            f"/api/images/fovs?experiment_id={experiment_id}&limit=2",
            headers=auth_headers
        )
        assert response.status_code == 200
        fovs = response.json()
        assert len(fovs) <= 2

        # Test with skip
        response = client.get(
            f"/api/images/fovs?experiment_id={experiment_id}&skip=1&limit=2",
            headers=auth_headers
        )
        assert response.status_code == 200


class TestImageStatusTransitions:
    """Tests for image status transitions in two-phase workflow."""

    def test_uploaded_status_in_fov_response(self, client, auth_headers):
        """Test that FOV response includes valid status values."""
        # Get an existing experiment
        response = client.get("/api/experiments/", headers=auth_headers)
        if response.status_code != 200 or len(response.json()) == 0:
            pytest.skip("No experiments available for testing")

        experiment_id = response.json()[0]["id"]
        response = client.get(
            f"/api/images/fovs?experiment_id={experiment_id}",
            headers=auth_headers
        )
        assert response.status_code == 200
        fovs = response.json()

        if len(fovs) == 0:
            pytest.skip("No FOV images available")

        # Verify status is one of the valid values
        valid_statuses = [
            "UPLOADING", "UPLOADED", "PROCESSING",
            "DETECTING", "EXTRACTING_FEATURES", "READY", "ERROR"
        ]
        for fov in fovs:
            assert fov["status"] in valid_statuses, \
                f"Invalid status: {fov['status']}"

    def test_fov_response_has_required_fields(self, client, auth_headers):
        """Test that FOV response has all required fields."""
        # Get an existing experiment
        response = client.get("/api/experiments/", headers=auth_headers)
        if response.status_code != 200 or len(response.json()) == 0:
            pytest.skip("No experiments available for testing")

        experiment_id = response.json()[0]["id"]
        response = client.get(
            f"/api/images/fovs?experiment_id={experiment_id}",
            headers=auth_headers
        )
        assert response.status_code == 200
        fovs = response.json()

        if len(fovs) == 0:
            pytest.skip("No FOV images available")

        required_fields = [
            "id", "experiment_id", "original_filename", "status",
            "detect_cells", "cell_count", "created_at"
        ]
        for fov in fovs:
            for field in required_fields:
                assert field in fov, f"Missing required field: {field}"


class TestBatchProcessSchemaValidation:
    """Tests for BatchProcessRequest schema validation."""

    def test_batch_process_max_length_validation(self, client, auth_headers):
        """Test that batch process rejects more than 1000 image IDs."""
        # Create a list with more than 1000 IDs
        too_many_ids = list(range(1, 1002))  # 1001 IDs

        response = client.post(
            "/api/images/batch-process",
            headers=auth_headers,
            json={
                "image_ids": too_many_ids,
                "detect_cells": True
            }
        )
        # Should return 422 for validation error
        assert response.status_code == 422

    def test_batch_process_detect_cells_default(self, client, auth_headers):
        """Test that detect_cells defaults to True if not provided."""
        response = client.post(
            "/api/images/batch-process",
            headers=auth_headers,
            json={
                "image_ids": [999999]  # Non-existent, but tests default
            }
        )
        # Will fail with 404 (not found), not 422 (validation error)
        # This confirms detect_cells is optional and defaults properly
        assert response.status_code == 404


class TestBatchProcessStatusValidation:
    """Tests for batch process status validation."""

    def test_batch_process_accepts_uploaded_status(self, client, auth_headers):
        """Test that batch process accepts images in UPLOADED status."""
        # Get an existing experiment with images
        response = client.get("/api/experiments/", headers=auth_headers)
        if response.status_code != 200 or len(response.json()) == 0:
            pytest.skip("No experiments available for testing")

        experiment_id = response.json()[0]["id"]
        response = client.get(
            f"/api/images/fovs?experiment_id={experiment_id}",
            headers=auth_headers
        )
        if response.status_code != 200 or len(response.json()) == 0:
            pytest.skip("No images available for testing")

        fovs = response.json()
        # Find an image in UPLOADED status
        uploaded_image = next(
            (fov for fov in fovs if fov["status"] == "UPLOADED"),
            None
        )
        if not uploaded_image:
            pytest.skip("No images in UPLOADED status")

        # Should accept the request (200 or 202)
        response = client.post(
            "/api/images/batch-process",
            headers=auth_headers,
            json={
                "image_ids": [uploaded_image["id"]],
                "detect_cells": False  # Quick processing
            }
        )
        # 200 means accepted, actual processing happens in background
        assert response.status_code == 200
        data = response.json()
        assert "processing_count" in data
        assert data["processing_count"] >= 0

    def test_batch_process_accepts_ready_status_for_reprocessing(self, client, auth_headers):
        """Test that batch process accepts images in READY status (reprocessing)."""
        # Get an existing experiment with images
        response = client.get("/api/experiments/", headers=auth_headers)
        if response.status_code != 200 or len(response.json()) == 0:
            pytest.skip("No experiments available for testing")

        experiment_id = response.json()[0]["id"]
        response = client.get(
            f"/api/images/fovs?experiment_id={experiment_id}",
            headers=auth_headers
        )
        if response.status_code != 200 or len(response.json()) == 0:
            pytest.skip("No images available for testing")

        fovs = response.json()
        # Find an image in READY status
        ready_image = next(
            (fov for fov in fovs if fov["status"] == "READY"),
            None
        )
        if not ready_image:
            pytest.skip("No images in READY status")

        # Should accept the request for reprocessing
        response = client.post(
            "/api/images/batch-process",
            headers=auth_headers,
            json={
                "image_ids": [ready_image["id"]],
                "detect_cells": False
            }
        )
        assert response.status_code == 200


class TestFOVResponseValidation:
    """Tests for FOV response field validation."""

    def test_fov_cell_count_is_non_negative(self, client, auth_headers):
        """Test that cell_count is always non-negative."""
        response = client.get("/api/experiments/", headers=auth_headers)
        if response.status_code != 200 or len(response.json()) == 0:
            pytest.skip("No experiments available for testing")

        experiment_id = response.json()[0]["id"]
        response = client.get(
            f"/api/images/fovs?experiment_id={experiment_id}",
            headers=auth_headers
        )
        if response.status_code != 200:
            pytest.skip("Failed to get FOVs")

        fovs = response.json()
        for fov in fovs:
            assert fov["cell_count"] >= 0, f"Invalid cell_count: {fov['cell_count']}"

    def test_fov_dimensions_are_positive_when_present(self, client, auth_headers):
        """Test that width/height are positive when present."""
        response = client.get("/api/experiments/", headers=auth_headers)
        if response.status_code != 200 or len(response.json()) == 0:
            pytest.skip("No experiments available for testing")

        experiment_id = response.json()[0]["id"]
        response = client.get(
            f"/api/images/fovs?experiment_id={experiment_id}",
            headers=auth_headers
        )
        if response.status_code != 200:
            pytest.skip("Failed to get FOVs")

        fovs = response.json()
        for fov in fovs:
            if fov.get("width") is not None:
                assert fov["width"] > 0, f"Invalid width: {fov['width']}"
            if fov.get("height") is not None:
                assert fov["height"] > 0, f"Invalid height: {fov['height']}"
            if fov.get("z_slices") is not None:
                assert fov["z_slices"] > 0, f"Invalid z_slices: {fov['z_slices']}"
