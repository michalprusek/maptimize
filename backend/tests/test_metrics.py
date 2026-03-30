"""Tests for metric endpoints.

Integration tests that verify CRUD, image import, and pairwise ranking
for the /api/metrics endpoints.

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d

Metrics are per-user. Tests must clean up any metrics they create.
"""
import pytest

from conftest import unique_name


class TestMetricCRUD:
    """Tests for basic metric CRUD operations."""

    def test_list_metrics(self, client, auth_headers):
        """GET /api/metrics returns MetricListResponse with items and total."""
        response = client.get("/api/metrics", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["total"], int)
        assert data["total"] == len(data["items"])

    def test_list_metrics_requires_auth(self, client):
        """GET /api/metrics without auth returns 401."""
        response = client.get("/api/metrics")
        assert response.status_code == 401

    def test_create_metric(self, client, auth_headers):
        """POST /api/metrics with valid data returns 201."""
        name = unique_name()
        metric_id = None
        try:
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            data = response.json()
            metric_id = data["id"]

            assert data["name"] == name
            assert data["image_count"] == 0
            assert data["comparison_count"] == 0
            assert "created_at" in data
            assert "updated_at" in data
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_create_metric_with_description(self, client, auth_headers):
        """POST /api/metrics with description returns 201."""
        name = unique_name()
        metric_id = None
        try:
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name, "description": "Test metric description"}
            )
            assert response.status_code == 201
            data = response.json()
            metric_id = data["id"]

            assert data["description"] == "Test metric description"
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_create_metric_requires_auth(self, client):
        """POST /api/metrics without auth returns 401."""
        response = client.post(
            "/api/metrics",
            json={"name": unique_name()}
        )
        assert response.status_code == 401

    def test_create_metric_empty_name_fails(self, client, auth_headers):
        """POST /api/metrics with empty name returns 422."""
        response = client.post(
            "/api/metrics",
            headers=auth_headers,
            json={"name": ""}
        )
        assert response.status_code == 422

    def test_get_metric_by_id(self, client, auth_headers):
        """GET /api/metrics/{id} returns metric details."""
        name = unique_name()
        metric_id = None
        try:
            # Create
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name, "description": "Get test"}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            # Fetch
            response = client.get(
                f"/api/metrics/{metric_id}",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == metric_id
            assert data["name"] == name
            assert data["description"] == "Get test"
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_get_nonexistent_metric_returns_404(self, client, auth_headers):
        """GET /api/metrics/{id} with invalid ID returns 404."""
        response = client.get("/api/metrics/999999", headers=auth_headers)
        assert response.status_code == 404

    def test_update_metric_name(self, client, auth_headers):
        """PATCH /api/metrics/{id} can update name."""
        name = unique_name()
        new_name = unique_name("UpdatedMetric")
        metric_id = None
        try:
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            # Update
            response = client.patch(
                f"/api/metrics/{metric_id}",
                headers=auth_headers,
                json={"name": new_name}
            )
            assert response.status_code == 200
            assert response.json()["name"] == new_name

            # Verify persisted
            response = client.get(
                f"/api/metrics/{metric_id}",
                headers=auth_headers
            )
            assert response.json()["name"] == new_name
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_update_metric_description(self, client, auth_headers):
        """PATCH /api/metrics/{id} can update description."""
        name = unique_name()
        metric_id = None
        try:
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            response = client.patch(
                f"/api/metrics/{metric_id}",
                headers=auth_headers,
                json={"description": "Updated description"}
            )
            assert response.status_code == 200
            assert response.json()["description"] == "Updated description"
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_update_nonexistent_metric_returns_404(self, client, auth_headers):
        """PATCH /api/metrics/{id} with invalid ID returns 404."""
        response = client.patch(
            "/api/metrics/999999",
            headers=auth_headers,
            json={"name": "ghost"}
        )
        assert response.status_code == 404

    def test_delete_metric(self, client, auth_headers):
        """DELETE /api/metrics/{id} returns 204 and removes the metric."""
        name = unique_name()
        response = client.post(
            "/api/metrics",
            headers=auth_headers,
            json={"name": name}
        )
        assert response.status_code == 201
        metric_id = response.json()["id"]

        # Delete
        response = client.delete(
            f"/api/metrics/{metric_id}",
            headers=auth_headers
        )
        assert response.status_code == 204

        # Verify gone
        response = client.get(
            f"/api/metrics/{metric_id}",
            headers=auth_headers
        )
        assert response.status_code == 404

    def test_delete_nonexistent_metric_returns_404(self, client, auth_headers):
        """DELETE /api/metrics/{id} with invalid ID returns 404."""
        response = client.delete("/api/metrics/999999", headers=auth_headers)
        assert response.status_code == 404


class TestMetricImages:
    """Tests for metric image management endpoints."""

    def test_list_metric_images_empty(self, client, auth_headers):
        """GET /api/metrics/{id}/images returns empty list for new metric."""
        name = unique_name()
        metric_id = None
        try:
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            response = client.get(
                f"/api/metrics/{metric_id}/images",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 0
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_list_experiments_for_import(self, client, auth_headers):
        """GET /api/metrics/{id}/experiments returns experiments available for import."""
        name = unique_name()
        metric_id = None
        try:
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            response = client.get(
                f"/api/metrics/{metric_id}/experiments",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)

            # Verify structure of experiment items
            if len(data) > 0:
                exp = data[0]
                assert "id" in exp
                assert "name" in exp
                assert "image_count" in exp
                assert "crop_count" in exp
                assert "already_imported" in exp
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_import_crops_from_experiment(self, client, auth_headers):
        """POST /api/metrics/{id}/images/import imports crops from experiments."""
        name = unique_name()
        metric_id = None
        try:
            # Create metric
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            # Find an experiment with crops
            response = client.get(
                f"/api/metrics/{metric_id}/experiments",
                headers=auth_headers
            )
            assert response.status_code == 200
            experiments = response.json()

            exp_with_crops = next(
                (e for e in experiments if e["crop_count"] > 0), None
            )
            if not exp_with_crops:
                pytest.skip("No experiments with crops available for import testing")

            # Import crops
            response = client.post(
                f"/api/metrics/{metric_id}/images/import",
                headers=auth_headers,
                json={"experiment_ids": [exp_with_crops["id"]]}
            )
            assert response.status_code == 200
            data = response.json()
            assert "imported_count" in data
            assert "skipped_count" in data
            assert data["imported_count"] >= 0
            assert data["skipped_count"] >= 0
            assert data["imported_count"] + data["skipped_count"] > 0

            # Verify images are now in the metric
            response = client.get(
                f"/api/metrics/{metric_id}/images",
                headers=auth_headers
            )
            assert response.status_code == 200
            images = response.json()
            assert len(images) == data["imported_count"]
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_import_crops_idempotent(self, client, auth_headers):
        """Importing the same experiment twice skips already imported crops."""
        name = unique_name()
        metric_id = None
        try:
            # Create metric
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            # Find an experiment with crops
            response = client.get(
                f"/api/metrics/{metric_id}/experiments",
                headers=auth_headers
            )
            experiments = response.json()
            exp_with_crops = next(
                (e for e in experiments if e["crop_count"] > 0), None
            )
            if not exp_with_crops:
                pytest.skip("No experiments with crops available")

            # First import
            response = client.post(
                f"/api/metrics/{metric_id}/images/import",
                headers=auth_headers,
                json={"experiment_ids": [exp_with_crops["id"]]}
            )
            assert response.status_code == 200
            first_import = response.json()

            # Second import of the same experiment
            response = client.post(
                f"/api/metrics/{metric_id}/images/import",
                headers=auth_headers,
                json={"experiment_ids": [exp_with_crops["id"]]}
            )
            assert response.status_code == 200
            second_import = response.json()

            # All crops should be skipped in second import
            assert second_import["imported_count"] == 0
            assert second_import["skipped_count"] == first_import["imported_count"]
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_import_crops_invalid_experiment_returns_404(self, client, auth_headers):
        """POST import with nonexistent experiment_id returns 404."""
        name = unique_name()
        metric_id = None
        try:
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            response = client.post(
                f"/api/metrics/{metric_id}/images/import",
                headers=auth_headers,
                json={"experiment_ids": [999999]}
            )
            assert response.status_code == 404
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_import_crops_empty_experiment_ids_returns_400(self, client, auth_headers):
        """POST import with empty experiment_ids returns 400."""
        name = unique_name()
        metric_id = None
        try:
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            response = client.post(
                f"/api/metrics/{metric_id}/images/import",
                headers=auth_headers,
                json={"experiment_ids": []}
            )
            assert response.status_code == 400
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_remove_metric_image(self, client, auth_headers):
        """DELETE /api/metrics/{id}/images/{image_id} removes an image."""
        name = unique_name()
        metric_id = None
        try:
            # Create metric
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            # Find experiment with crops and import
            response = client.get(
                f"/api/metrics/{metric_id}/experiments",
                headers=auth_headers
            )
            experiments = response.json()
            exp_with_crops = next(
                (e for e in experiments if e["crop_count"] > 0), None
            )
            if not exp_with_crops:
                pytest.skip("No experiments with crops available")

            response = client.post(
                f"/api/metrics/{metric_id}/images/import",
                headers=auth_headers,
                json={"experiment_ids": [exp_with_crops["id"]]}
            )
            assert response.status_code == 200
            if response.json()["imported_count"] == 0:
                pytest.skip("No crops imported")

            # Get images and delete the first one
            response = client.get(
                f"/api/metrics/{metric_id}/images",
                headers=auth_headers
            )
            images = response.json()
            assert len(images) > 0
            image_to_delete = images[0]["id"]
            count_before = len(images)

            response = client.delete(
                f"/api/metrics/{metric_id}/images/{image_to_delete}",
                headers=auth_headers
            )
            assert response.status_code == 204

            # Verify image removed
            response = client.get(
                f"/api/metrics/{metric_id}/images",
                headers=auth_headers
            )
            assert len(response.json()) == count_before - 1
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_remove_nonexistent_image_returns_404(self, client, auth_headers):
        """DELETE /api/metrics/{id}/images/{image_id} with bad ID returns 404."""
        name = unique_name()
        metric_id = None
        try:
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            response = client.delete(
                f"/api/metrics/{metric_id}/images/999999",
                headers=auth_headers
            )
            assert response.status_code == 404
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)


class TestMetricComparison:
    """Tests for metric comparison (ranking) endpoints.

    These tests require at least 2 images imported into a metric.
    If no experiments with crops are available, tests are skipped.
    """

    @pytest.fixture
    def metric_with_images(self, client, auth_headers):
        """Create a metric and import crops, yielding (metric_id, image_count).

        Cleans up the metric after the test.
        """
        name = unique_name("CompMetric")
        response = client.post(
            "/api/metrics",
            headers=auth_headers,
            json={"name": name}
        )
        assert response.status_code == 201
        metric_id = response.json()["id"]

        # Find experiment with crops
        response = client.get(
            f"/api/metrics/{metric_id}/experiments",
            headers=auth_headers
        )
        experiments = response.json()
        exp_with_crops = next(
            (e for e in experiments if e["crop_count"] >= 2), None
        )

        image_count = 0
        if exp_with_crops:
            response = client.post(
                f"/api/metrics/{metric_id}/images/import",
                headers=auth_headers,
                json={"experiment_ids": [exp_with_crops["id"]]}
            )
            if response.status_code == 200:
                image_count = response.json()["imported_count"]

        yield metric_id, image_count

        # Cleanup: undo all comparisons then delete metric
        for _ in range(100):  # Safety limit
            resp = client.post(
                f"/api/metrics/{metric_id}/undo",
                headers=auth_headers
            )
            if resp.status_code == 404:
                break

        client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_get_pair(self, client, auth_headers, metric_with_images):
        """GET /api/metrics/{id}/pair returns a pair of images to compare."""
        metric_id, image_count = metric_with_images
        if image_count < 2:
            pytest.skip("Not enough images for comparison (need >= 2)")

        response = client.get(
            f"/api/metrics/{metric_id}/pair",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()

        assert "image_a" in data
        assert "image_b" in data
        assert "comparison_number" in data
        assert "total_comparisons" in data

        assert "id" in data["image_a"]
        assert "id" in data["image_b"]
        assert data["image_a"]["id"] != data["image_b"]["id"]

    def test_get_pair_not_enough_images(self, client, auth_headers):
        """GET /api/metrics/{id}/pair with < 2 images returns 400."""
        name = unique_name()
        metric_id = None
        try:
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            response = client.get(
                f"/api/metrics/{metric_id}/pair",
                headers=auth_headers
            )
            assert response.status_code == 400
            assert "not enough" in response.json()["detail"].lower()
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_submit_comparison(self, client, auth_headers, metric_with_images):
        """POST /api/metrics/{id}/compare submits a comparison."""
        metric_id, image_count = metric_with_images
        if image_count < 2:
            pytest.skip("Not enough images for comparison")

        # Get a pair
        response = client.get(
            f"/api/metrics/{metric_id}/pair",
            headers=auth_headers
        )
        assert response.status_code == 200
        pair = response.json()
        image_a_id = pair["image_a"]["id"]
        image_b_id = pair["image_b"]["id"]

        # Submit comparison
        response = client.post(
            f"/api/metrics/{metric_id}/compare",
            headers=auth_headers,
            json={
                "image_a_id": image_a_id,
                "image_b_id": image_b_id,
                "winner_id": image_a_id,
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["metric_id"] == metric_id
        assert data["image_a_id"] == image_a_id
        assert data["image_b_id"] == image_b_id
        assert data["winner_id"] == image_a_id
        assert "id" in data
        assert "created_at" in data

    def test_undo_comparison(self, client, auth_headers, metric_with_images):
        """POST /api/metrics/{id}/undo undoes the last comparison."""
        metric_id, image_count = metric_with_images
        if image_count < 2:
            pytest.skip("Not enough images for comparison")

        # Get a pair and submit comparison
        response = client.get(
            f"/api/metrics/{metric_id}/pair",
            headers=auth_headers
        )
        assert response.status_code == 200
        pair = response.json()
        image_a_id = pair["image_a"]["id"]
        image_b_id = pair["image_b"]["id"]

        response = client.post(
            f"/api/metrics/{metric_id}/compare",
            headers=auth_headers,
            json={
                "image_a_id": image_a_id,
                "image_b_id": image_b_id,
                "winner_id": image_a_id,
            }
        )
        assert response.status_code == 200
        comparison_id = response.json()["id"]

        # Undo
        response = client.post(
            f"/api/metrics/{metric_id}/undo",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == comparison_id

    def test_undo_with_no_comparisons_returns_404(self, client, auth_headers):
        """POST /api/metrics/{id}/undo with no comparisons returns 404."""
        name = unique_name()
        metric_id = None
        try:
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            response = client.post(
                f"/api/metrics/{metric_id}/undo",
                headers=auth_headers
            )
            assert response.status_code == 404
            assert "no comparison" in response.json()["detail"].lower()
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_leaderboard_empty(self, client, auth_headers):
        """GET /api/metrics/{id}/leaderboard returns empty ranking for new metric."""
        name = unique_name()
        metric_id = None
        try:
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            response = client.get(
                f"/api/metrics/{metric_id}/leaderboard",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert "items" in data
            assert "total" in data
            assert "page" in data
            assert "per_page" in data
            assert data["total"] == 0
            assert data["items"] == []
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_leaderboard_after_comparison(self, client, auth_headers, metric_with_images):
        """GET /api/metrics/{id}/leaderboard returns ranked items after comparison."""
        metric_id, image_count = metric_with_images
        if image_count < 2:
            pytest.skip("Not enough images for comparison")

        # Get a pair and submit comparison
        response = client.get(
            f"/api/metrics/{metric_id}/pair",
            headers=auth_headers
        )
        assert response.status_code == 200
        pair = response.json()

        response = client.post(
            f"/api/metrics/{metric_id}/compare",
            headers=auth_headers,
            json={
                "image_a_id": pair["image_a"]["id"],
                "image_b_id": pair["image_b"]["id"],
                "winner_id": pair["image_a"]["id"],
            }
        )
        assert response.status_code == 200

        # Check leaderboard
        response = client.get(
            f"/api/metrics/{metric_id}/leaderboard",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 2

        # Verify item structure
        for item in data["items"]:
            assert "rank" in item
            assert "metric_image_id" in item
            assert "mu" in item
            assert "sigma" in item
            assert "comparison_count" in item
            assert item["rank"] >= 1

    def test_progress_empty_metric(self, client, auth_headers):
        """GET /api/metrics/{id}/progress returns convergence info for new metric."""
        name = unique_name()
        metric_id = None
        try:
            response = client.post(
                "/api/metrics",
                headers=auth_headers,
                json={"name": name}
            )
            assert response.status_code == 201
            metric_id = response.json()["id"]

            response = client.get(
                f"/api/metrics/{metric_id}/progress",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()

            assert "total_comparisons" in data
            assert "convergence_percent" in data
            assert "estimated_remaining" in data
            assert "average_sigma" in data
            assert "target_sigma" in data
            assert "phase" in data
            assert "image_count" in data
            assert data["total_comparisons"] == 0
            assert data["image_count"] == 0
            assert data["phase"] in ("exploration", "exploitation")
            assert 0 <= data["convergence_percent"] <= 100
        finally:
            if metric_id:
                client.delete(f"/api/metrics/{metric_id}", headers=auth_headers)

    def test_progress_after_comparison(self, client, auth_headers, metric_with_images):
        """GET /api/metrics/{id}/progress shows updated counts after comparison."""
        metric_id, image_count = metric_with_images
        if image_count < 2:
            pytest.skip("Not enough images for comparison")

        # Get initial progress
        response = client.get(
            f"/api/metrics/{metric_id}/progress",
            headers=auth_headers
        )
        assert response.status_code == 200
        before = response.json()

        # Submit a comparison
        response = client.get(
            f"/api/metrics/{metric_id}/pair",
            headers=auth_headers
        )
        assert response.status_code == 200
        pair = response.json()

        response = client.post(
            f"/api/metrics/{metric_id}/compare",
            headers=auth_headers,
            json={
                "image_a_id": pair["image_a"]["id"],
                "image_b_id": pair["image_b"]["id"],
                "winner_id": pair["image_a"]["id"],
            }
        )
        assert response.status_code == 200

        # Check progress updated
        response = client.get(
            f"/api/metrics/{metric_id}/progress",
            headers=auth_headers
        )
        assert response.status_code == 200
        after = response.json()

        assert after["total_comparisons"] == before["total_comparisons"] + 1
        assert after["image_count"] == image_count

    def test_compare_invalid_winner_returns_422(self, client, auth_headers, metric_with_images):
        """POST compare with winner not in pair returns 422."""
        metric_id, image_count = metric_with_images
        if image_count < 2:
            pytest.skip("Not enough images for comparison")

        response = client.get(
            f"/api/metrics/{metric_id}/pair",
            headers=auth_headers
        )
        assert response.status_code == 200
        pair = response.json()

        # Use an invalid winner_id (not one of the pair)
        response = client.post(
            f"/api/metrics/{metric_id}/compare",
            headers=auth_headers,
            json={
                "image_a_id": pair["image_a"]["id"],
                "image_b_id": pair["image_b"]["id"],
                "winner_id": 999999,
            }
        )
        # Pydantic model_validator raises ValueError -> 422
        assert response.status_code == 422
