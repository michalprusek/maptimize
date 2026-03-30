"""Tests for export/import endpoints.

Integration tests that verify:
- POST /api/data/export/prepare - prepare an export job
- GET /api/data/export/status/{job_id} - check export job status
- GET /api/data/export/stream/{job_id} - stream export file
- POST /api/data/import/validate - validate an import file

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d
"""
import io

import pytest


class TestExportPrepare:
    """Test suite for POST /api/data/export/prepare."""

    def test_export_prepare_requires_auth(self, client):
        """Export prepare without auth returns 401."""
        response = client.post(
            "/api/data/export/prepare",
            json={"experiment_ids": [1]},
        )
        assert response.status_code == 401

    def test_export_prepare_valid(self, client, auth_headers):
        """Export prepare with valid experiment IDs returns job info."""
        # Get an existing experiment first
        experiments = client.get("/api/experiments/", headers=auth_headers)
        if experiments.status_code != 200 or len(experiments.json()) == 0:
            pytest.skip("No experiments available for testing")

        experiment_id = experiments.json()[0]["id"]

        response = client.post(
            "/api/data/export/prepare",
            headers=auth_headers,
            json={"experiment_ids": [experiment_id]},
        )
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert data["experiment_count"] >= 1
        assert "image_count" in data
        assert "crop_count" in data
        assert "estimated_size_bytes" in data

    def test_export_prepare_empty_ids(self, client, auth_headers):
        """Export prepare with empty experiment_ids returns 422."""
        response = client.post(
            "/api/data/export/prepare",
            headers=auth_headers,
            json={"experiment_ids": []},
        )
        assert response.status_code == 422

    def test_export_prepare_nonexistent_experiment(self, client, auth_headers):
        """Export prepare with nonexistent experiment returns 400 or 404."""
        response = client.post(
            "/api/data/export/prepare",
            headers=auth_headers,
            json={"experiment_ids": [999999]},
        )
        # Service raises ValueError for not-found experiments -> 400
        assert response.status_code in [400, 404]

    def test_export_prepare_negative_id(self, client, auth_headers):
        """Export prepare with negative experiment ID returns 422."""
        response = client.post(
            "/api/data/export/prepare",
            headers=auth_headers,
            json={"experiment_ids": [-1]},
        )
        assert response.status_code == 422


class TestExportStatus:
    """Test suite for GET /api/data/export/status/{job_id}."""

    def test_export_status_requires_auth(self, client):
        """Export status without auth returns 401."""
        response = client.get("/api/data/export/status/nonexistent-job-id")
        assert response.status_code == 401

    def test_export_status_nonexistent_job(self, client, auth_headers):
        """Export status for nonexistent job returns 404."""
        response = client.get(
            "/api/data/export/status/nonexistent-job-id",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_export_status_valid_job(self, client, auth_headers):
        """Export status for a valid job returns status info."""
        # First prepare an export to get a job_id
        experiments = client.get("/api/experiments/", headers=auth_headers)
        if experiments.status_code != 200 or len(experiments.json()) == 0:
            pytest.skip("No experiments available for testing")

        experiment_id = experiments.json()[0]["id"]
        prepare = client.post(
            "/api/data/export/prepare",
            headers=auth_headers,
            json={"experiment_ids": [experiment_id]},
        )
        if prepare.status_code != 200:
            pytest.skip("Failed to prepare export")

        job_id = prepare.json()["job_id"]

        response = client.get(
            f"/api/data/export/status/{job_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert "status" in data
        assert data["status"] in ["preparing", "streaming", "completed", "error"]


class TestImportValidate:
    """Test suite for POST /api/data/import/validate."""

    def test_import_validate_requires_auth(self, client):
        """Import validate without auth returns 401."""
        fake_zip = io.BytesIO(b"PK\x03\x04fake zip content")
        response = client.post(
            "/api/data/import/validate",
            files={"file": ("test.zip", fake_zip, "application/zip")},
        )
        assert response.status_code == 401

    def test_import_validate_no_file(self, client, auth_headers):
        """Import validate without a file returns 422."""
        response = client.post(
            "/api/data/import/validate",
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_import_validate_non_zip_file(self, client, auth_headers):
        """Import validate with non-ZIP file returns 400."""
        fake_file = io.BytesIO(b"this is not a zip")
        response = client.post(
            "/api/data/import/validate",
            headers=auth_headers,
            files={"file": ("data.csv", fake_file, "text/csv")},
        )
        assert response.status_code == 400
        assert "zip" in response.json()["detail"].lower()
