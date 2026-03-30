"""Tests for experiment endpoints.

Integration tests that verify:
- GET /api/experiments - list experiments
- POST /api/experiments - create experiment
- GET /api/experiments/{id} - get experiment detail
- PATCH /api/experiments/{id} - update experiment
- DELETE /api/experiments/{id} - delete experiment
- PATCH /api/experiments/{id}/protein - update protein assignment

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d
"""
import pytest

from conftest import unique_name as _unique_name


@pytest.fixture
def created_experiment(client, auth_headers):
    """Create an experiment and clean it up after the test.

    Yields the full response dict from the creation call.
    """
    name = _unique_name("Fixture Exp")
    response = client.post(
        "/api/experiments",
        headers=auth_headers,
        json={"name": name, "description": "Created by test fixture"},
    )
    assert response.status_code == 201
    data = response.json()
    yield data

    # Cleanup - ignore errors (test may have already deleted it)
    client.delete(f"/api/experiments/{data['id']}", headers=auth_headers)


class TestExperimentList:
    """Test suite for GET /api/experiments."""

    def test_list_returns_array(self, client, auth_headers):
        """GET /api/experiments returns a JSON array."""
        response = client.get("/api/experiments", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_requires_auth(self, client):
        """GET /api/experiments without auth returns 401."""
        response = client.get("/api/experiments")
        assert response.status_code == 401

    def test_list_pagination_limit(self, client, auth_headers):
        """GET /api/experiments respects the limit parameter."""
        response = client.get(
            "/api/experiments",
            headers=auth_headers,
            params={"limit": 2},
        )
        assert response.status_code == 200
        assert len(response.json()) <= 2

    def test_list_pagination_skip(self, client, auth_headers):
        """GET /api/experiments respects the skip parameter."""
        # Get all experiments
        all_resp = client.get("/api/experiments", headers=auth_headers)
        assert all_resp.status_code == 200
        all_data = all_resp.json()

        if len(all_data) < 2:
            pytest.skip("Need at least 2 experiments to test skip")

        # Skip the first one
        skipped = client.get(
            "/api/experiments",
            headers=auth_headers,
            params={"skip": 1},
        )
        assert skipped.status_code == 200
        skipped_data = skipped.json()
        assert len(skipped_data) == len(all_data) - 1

    def test_list_includes_counts(self, client, auth_headers):
        """Each experiment in the list includes image_count and cell_count."""
        response = client.get("/api/experiments", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        if len(data) == 0:
            pytest.skip("No experiments to check counts on")

        first = data[0]
        assert "image_count" in first
        assert "cell_count" in first
        assert isinstance(first["image_count"], int)
        assert isinstance(first["cell_count"], int)
        assert first["image_count"] >= 0
        assert first["cell_count"] >= 0


class TestExperimentCreate:
    """Test suite for POST /api/experiments."""

    def test_create_valid(self, client, auth_headers):
        """Create an experiment with a valid name returns 201."""
        name = _unique_name()
        response = client.post(
            "/api/experiments",
            headers=auth_headers,
            json={"name": name},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == name
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

        # Cleanup
        client.delete(f"/api/experiments/{data['id']}", headers=auth_headers)

    def test_create_requires_auth(self, client):
        """POST /api/experiments without auth returns 401."""
        response = client.post(
            "/api/experiments",
            json={"name": "Unauthorized"},
        )
        assert response.status_code == 401

    def test_create_empty_name(self, client, auth_headers):
        """Creating an experiment with empty name returns 422."""
        response = client.post(
            "/api/experiments",
            headers=auth_headers,
            json={"name": ""},
        )
        assert response.status_code == 422

    def test_create_missing_name(self, client, auth_headers):
        """Creating an experiment without a name returns 422."""
        response = client.post(
            "/api/experiments",
            headers=auth_headers,
            json={},
        )
        assert response.status_code == 422

    def test_create_with_description(self, client, auth_headers):
        """Creating an experiment with a description stores it."""
        name = _unique_name()
        description = "A detailed description of this experiment."
        response = client.post(
            "/api/experiments",
            headers=auth_headers,
            json={"name": name, "description": description},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["description"] == description

        # Cleanup
        client.delete(f"/api/experiments/{data['id']}", headers=auth_headers)

    def test_create_default_counts(self, client, auth_headers):
        """A freshly created experiment has zero image and cell counts."""
        name = _unique_name()
        response = client.post(
            "/api/experiments",
            headers=auth_headers,
            json={"name": name},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["image_count"] == 0
        assert data["cell_count"] == 0

        # Cleanup
        client.delete(f"/api/experiments/{data['id']}", headers=auth_headers)


class TestExperimentDetail:
    """Test suite for GET /api/experiments/{id}."""

    def test_get_by_id(self, client, auth_headers, created_experiment):
        """GET /api/experiments/{id} returns the experiment details."""
        exp_id = created_experiment["id"]
        response = client.get(
            f"/api/experiments/{exp_id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == exp_id
        assert data["name"] == created_experiment["name"]
        assert "images" in data  # Detail response includes images list

    def test_get_nonexistent(self, client, auth_headers):
        """GET /api/experiments/{id} for a nonexistent ID returns 404."""
        response = client.get(
            "/api/experiments/999999",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_get_requires_auth(self, client, created_experiment):
        """GET /api/experiments/{id} without auth returns 401."""
        response = client.get(f"/api/experiments/{created_experiment['id']}")
        assert response.status_code == 401


class TestExperimentUpdate:
    """Test suite for PATCH /api/experiments/{id}."""

    def test_update_name(self, client, auth_headers, created_experiment):
        """PATCH /api/experiments/{id} can update the name."""
        exp_id = created_experiment["id"]
        new_name = _unique_name("Renamed")
        response = client.patch(
            f"/api/experiments/{exp_id}",
            headers=auth_headers,
            json={"name": new_name},
        )

        assert response.status_code == 200
        assert response.json()["name"] == new_name

    def test_update_description(self, client, auth_headers, created_experiment):
        """PATCH /api/experiments/{id} can update the description."""
        exp_id = created_experiment["id"]
        new_desc = "Updated description text."
        response = client.patch(
            f"/api/experiments/{exp_id}",
            headers=auth_headers,
            json={"description": new_desc},
        )

        assert response.status_code == 200
        assert response.json()["description"] == new_desc

    def test_update_nonexistent(self, client, auth_headers):
        """PATCH /api/experiments/{id} for a nonexistent ID returns 404."""
        response = client.patch(
            "/api/experiments/999999",
            headers=auth_headers,
            json={"name": "Ghost"},
        )
        assert response.status_code == 404

    def test_update_requires_auth(self, client, created_experiment):
        """PATCH /api/experiments/{id} without auth returns 401."""
        response = client.patch(
            f"/api/experiments/{created_experiment['id']}",
            json={"name": "Hacked"},
        )
        assert response.status_code == 401


class TestExperimentDelete:
    """Test suite for DELETE /api/experiments/{id}."""

    def test_delete_existing(self, client, auth_headers):
        """DELETE /api/experiments/{id} removes the experiment and returns 204."""
        # Create an experiment specifically for deletion
        name = _unique_name("To Delete")
        create_resp = client.post(
            "/api/experiments",
            headers=auth_headers,
            json={"name": name},
        )
        assert create_resp.status_code == 201
        exp_id = create_resp.json()["id"]

        # Delete it
        delete_resp = client.delete(
            f"/api/experiments/{exp_id}",
            headers=auth_headers,
        )
        assert delete_resp.status_code == 204

        # Verify it is gone
        get_resp = client.get(
            f"/api/experiments/{exp_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 404

    def test_delete_nonexistent(self, client, auth_headers):
        """DELETE /api/experiments/{id} for a nonexistent ID returns 404."""
        response = client.delete(
            "/api/experiments/999999",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_delete_requires_auth(self, client, created_experiment):
        """DELETE /api/experiments/{id} without auth returns 401."""
        response = client.delete(
            f"/api/experiments/{created_experiment['id']}",
        )
        assert response.status_code == 401


class TestExperimentProtein:
    """Test suite for PATCH /api/experiments/{id}/protein."""

    def _get_protein_id(self, client, auth_headers):
        """Get a valid protein ID from the API, or skip if none available."""
        response = client.get("/api/proteins", headers=auth_headers)
        if response.status_code != 200 or len(response.json()) == 0:
            pytest.skip("No proteins available in the system")
        return response.json()[0]["id"]

    def test_assign_protein(self, client, auth_headers, created_experiment):
        """PATCH /api/experiments/{id}/protein assigns a protein."""
        protein_id = self._get_protein_id(client, auth_headers)
        exp_id = created_experiment["id"]

        response = client.patch(
            f"/api/experiments/{exp_id}/protein",
            headers=auth_headers,
            params={"map_protein_id": protein_id},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["map_protein_id"] == protein_id
        assert data["id"] == exp_id

    def test_clear_protein(self, client, auth_headers, created_experiment):
        """PATCH /api/experiments/{id}/protein with null clears the assignment."""
        exp_id = created_experiment["id"]

        response = client.patch(
            f"/api/experiments/{exp_id}/protein",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["map_protein_id"] is None

    def test_assign_nonexistent_protein(self, client, auth_headers, created_experiment):
        """Assigning a nonexistent protein ID returns 404."""
        exp_id = created_experiment["id"]

        response = client.patch(
            f"/api/experiments/{exp_id}/protein",
            headers=auth_headers,
            params={"map_protein_id": 999999},
        )
        assert response.status_code == 404

    def test_protein_requires_auth(self, client, created_experiment):
        """PATCH /api/experiments/{id}/protein without auth returns 401."""
        response = client.patch(
            f"/api/experiments/{created_experiment['id']}/protein",
            params={"map_protein_id": 1},
        )
        assert response.status_code == 401
