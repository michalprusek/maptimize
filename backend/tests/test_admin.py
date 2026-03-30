"""Tests for admin panel endpoints.

Integration tests that verify:
- GET /api/admin/stats - system-wide statistics
- GET /api/admin/stats/timeline - timeline statistics
- GET /api/admin/users - paginated user list
- GET /api/admin/users/{id} - user detail
- PATCH /api/admin/users/{id} - update user
- POST /api/admin/users/{id}/reset-password - reset password
- DELETE /api/admin/users/{id} - delete user
- GET /api/admin/users/{id}/conversations - user chat threads
- GET /api/admin/users/{id}/experiments - user experiments
- GET /api/admin/gpu/status - GPU model info

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d
"""
import pytest


# admin_headers fixture is provided by conftest.py (session-scoped)


class TestAdminAuth:
    """Test that admin endpoints enforce admin-only access."""

    def test_stats_requires_auth(self, client):
        """Admin stats without any auth returns 401."""
        response = client.get("/api/admin/stats")
        assert response.status_code == 401

    def test_stats_forbidden_for_regular_user(self, client, auth_headers):
        """Regular user cannot access admin stats -> 403."""
        response = client.get("/api/admin/stats", headers=auth_headers)
        assert response.status_code == 403

    def test_users_requires_auth(self, client):
        """Admin users list without auth returns 401."""
        response = client.get("/api/admin/users")
        assert response.status_code == 401

    def test_users_forbidden_for_regular_user(self, client, auth_headers):
        """Regular user cannot access admin users list -> 403."""
        response = client.get("/api/admin/users", headers=auth_headers)
        assert response.status_code == 403

    def test_gpu_status_requires_auth(self, client):
        """GPU status without auth returns 401."""
        response = client.get("/api/admin/gpu/status")
        assert response.status_code == 401

    def test_gpu_status_forbidden_for_regular_user(self, client, auth_headers):
        """Regular user cannot access GPU status -> 403."""
        response = client.get("/api/admin/gpu/status", headers=auth_headers)
        assert response.status_code == 403


class TestAdminStats:
    """Test suite for admin statistics endpoints."""

    def test_system_stats_has_expected_fields(self, client, admin_headers):
        """GET /api/admin/stats returns all expected fields."""
        response = client.get("/api/admin/stats", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()

        expected_fields = [
            "total_users",
            "total_experiments",
            "total_images",
            "total_documents",
            "total_storage_bytes",
            "admin_count",
            "researcher_count",
            "viewer_count",
            "images_storage_bytes",
            "documents_storage_bytes",
        ]
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"
            assert isinstance(data[field], int), f"{field} should be int"
            assert data[field] >= 0, f"{field} should be non-negative"

    def test_system_stats_consistency(self, client, admin_headers):
        """Admin stats user counts are consistent."""
        response = client.get("/api/admin/stats", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()

        role_sum = data["admin_count"] + data["researcher_count"] + data["viewer_count"]
        assert data["total_users"] == role_sum, (
            f"total_users ({data['total_users']}) != sum of roles ({role_sum})"
        )

    def test_timeline_returns_array(self, client, admin_headers):
        """GET /api/admin/stats/timeline returns timeline data."""
        response = client.get(
            "/api/admin/stats/timeline?days=7",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "period_days" in data
        assert isinstance(data["data"], list)
        assert data["period_days"] == 7

        # Each point should have date, registrations, active_users
        if len(data["data"]) > 0:
            point = data["data"][0]
            assert "date" in point
            assert "registrations" in point
            assert "active_users" in point

    def test_timeline_default_days(self, client, admin_headers):
        """Timeline without explicit days parameter uses default (30)."""
        response = client.get(
            "/api/admin/stats/timeline",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["period_days"] == 30


class TestAdminUsers:
    """Test suite for admin user management endpoints."""

    def test_list_users(self, client, admin_headers):
        """GET /api/admin/users returns paginated user list."""
        response = client.get("/api/admin/users", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert "total_pages" in data
        assert isinstance(data["users"], list)
        assert data["total"] >= 1  # At least the admin user

    def test_list_users_pagination(self, client, admin_headers):
        """User list respects pagination parameters."""
        response = client.get(
            "/api/admin/users?page=1&page_size=2",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["users"]) <= 2
        assert data["page"] == 1
        assert data["page_size"] == 2

    def test_list_users_search(self, client, admin_headers):
        """User list supports search by name or email."""
        # Search for something very unlikely to match
        response = client.get(
            "/api/admin/users?search=zzz_nonexistent_zzz",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert len(data["users"]) == 0

    def test_get_user_detail(self, client, admin_headers):
        """GET /api/admin/users/{id} returns user detail."""
        # First get the user list to find a valid user ID
        users_response = client.get("/api/admin/users", headers=admin_headers)
        assert users_response.status_code == 200
        users = users_response.json()["users"]
        assert len(users) > 0

        user_id = users[0]["id"]
        response = client.get(
            f"/api/admin/users/{user_id}",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == user_id
        assert "email" in data
        assert "name" in data
        assert "role" in data
        assert "experiment_count" in data
        assert "image_count" in data
        assert "total_storage_bytes" in data

    def test_get_nonexistent_user(self, client, admin_headers):
        """GET /api/admin/users/{id} for nonexistent user returns 404."""
        response = client.get(
            "/api/admin/users/999999",
            headers=admin_headers,
        )
        assert response.status_code == 404


class TestAdminUserData:
    """Test suite for fetching user-specific data via admin endpoints."""

    def _get_first_user_id(self, client, admin_headers):
        """Helper: get the first user ID from the admin users list."""
        response = client.get("/api/admin/users", headers=admin_headers)
        if response.status_code != 200 or len(response.json()["users"]) == 0:
            pytest.skip("No users available")
        return response.json()["users"][0]["id"]

    def test_get_user_conversations(self, client, admin_headers):
        """GET /api/admin/users/{id}/conversations returns thread list."""
        user_id = self._get_first_user_id(client, admin_headers)
        response = client.get(
            f"/api/admin/users/{user_id}/conversations",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "threads" in data
        assert "total" in data
        assert isinstance(data["threads"], list)

    def test_get_user_conversations_nonexistent(self, client, admin_headers):
        """Conversations for nonexistent user returns 404."""
        response = client.get(
            "/api/admin/users/999999/conversations",
            headers=admin_headers,
        )
        assert response.status_code == 404

    def test_get_user_experiments(self, client, admin_headers):
        """GET /api/admin/users/{id}/experiments returns experiment list."""
        user_id = self._get_first_user_id(client, admin_headers)
        response = client.get(
            f"/api/admin/users/{user_id}/experiments",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "experiments" in data
        assert "total" in data
        assert isinstance(data["experiments"], list)

    def test_get_user_experiments_nonexistent(self, client, admin_headers):
        """Experiments for nonexistent user returns 404."""
        response = client.get(
            "/api/admin/users/999999/experiments",
            headers=admin_headers,
        )
        assert response.status_code == 404


class TestAdminGPU:
    """Test suite for GPU status endpoint."""

    def test_gpu_status_returns_info(self, client, admin_headers):
        """GET /api/admin/gpu/status returns GPU model info with expected structure."""
        response = client.get("/api/admin/gpu/status", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        # Validate top-level structure
        assert "gpu" in data
        assert "config" in data
        assert "models" in data
        assert "total_estimated_usage_mb" in data
        # Validate config structure
        config = data["config"]
        assert "memory_limit_mb" in config
        assert "idle_timeout_seconds" in config
        assert "cleanup_interval_seconds" in config
        # Validate models is a list with expected fields
        assert isinstance(data["models"], list)
        for model in data["models"]:
            assert "name" in model
            assert "state" in model
            assert "estimated_vram_mb" in model
            assert model["state"] in ["unloaded", "loading", "loaded"]
