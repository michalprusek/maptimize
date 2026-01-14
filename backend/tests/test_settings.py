"""Tests for settings endpoints.

Tests the user settings API including:
- GET/PATCH /api/settings - display preferences
- PATCH /api/settings/profile - profile updates
- POST /api/settings/password - password changes

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d
"""
import pytest


class TestSettingsEndpoints:
    """Test suite for settings API endpoints."""

    def test_get_settings(self, client, auth_headers):
        """GET /api/settings returns current user settings."""
        response = client.get("/api/settings", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        # Verify required fields exist
        assert "display_mode" in data
        assert "theme" in data
        assert "language" in data

        # Verify valid enum values
        assert data["display_mode"] in ["grayscale", "inverted", "green", "fire", "hilo"]
        assert data["theme"] in ["dark", "light"]
        assert data["language"] in ["en", "fr"]

    def test_update_display_mode(self, client, auth_headers):
        """PATCH /api/settings updates display mode."""
        # First get current settings to restore later
        original = client.get("/api/settings", headers=auth_headers).json()

        # Test updating display mode
        for mode in ["grayscale", "inverted", "green", "fire", "hilo"]:
            response = client.patch(
                "/api/settings",
                headers=auth_headers,
                json={"display_mode": mode}
            )
            assert response.status_code == 200
            assert response.json()["display_mode"] == mode

        # Restore original setting
        client.patch(
            "/api/settings",
            headers=auth_headers,
            json={"display_mode": original["display_mode"]}
        )

    def test_update_theme(self, client, auth_headers):
        """PATCH /api/settings updates theme."""
        original = client.get("/api/settings", headers=auth_headers).json()

        for theme in ["dark", "light"]:
            response = client.patch(
                "/api/settings",
                headers=auth_headers,
                json={"theme": theme}
            )
            assert response.status_code == 200
            assert response.json()["theme"] == theme

        # Restore original
        client.patch(
            "/api/settings",
            headers=auth_headers,
            json={"theme": original["theme"]}
        )

    def test_update_language(self, client, auth_headers):
        """PATCH /api/settings updates language."""
        original = client.get("/api/settings", headers=auth_headers).json()

        for lang in ["en", "fr"]:
            response = client.patch(
                "/api/settings",
                headers=auth_headers,
                json={"language": lang}
            )
            assert response.status_code == 200
            assert response.json()["language"] == lang

        # Restore original
        client.patch(
            "/api/settings",
            headers=auth_headers,
            json={"language": original["language"]}
        )

    def test_update_multiple_settings(self, client, auth_headers):
        """PATCH /api/settings can update multiple settings at once."""
        original = client.get("/api/settings", headers=auth_headers).json()

        response = client.patch(
            "/api/settings",
            headers=auth_headers,
            json={
                "display_mode": "fire",
                "theme": "light",
                "language": "fr"
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["display_mode"] == "fire"
        assert data["theme"] == "light"
        assert data["language"] == "fr"

        # Restore original settings
        client.patch(
            "/api/settings",
            headers=auth_headers,
            json={
                "display_mode": original["display_mode"],
                "theme": original["theme"],
                "language": original["language"]
            }
        )

    def test_settings_require_auth(self, client):
        """Settings endpoints require authentication."""
        # GET without auth
        response = client.get("/api/settings")
        assert response.status_code in [401, 403]

        # PATCH without auth
        response = client.patch("/api/settings", json={"theme": "dark"})
        assert response.status_code in [401, 403]

    def test_invalid_display_mode(self, client, auth_headers):
        """PATCH /api/settings rejects invalid display mode."""
        response = client.patch(
            "/api/settings",
            headers=auth_headers,
            json={"display_mode": "invalid_mode"}
        )
        assert response.status_code == 422  # Validation error

    def test_invalid_theme(self, client, auth_headers):
        """PATCH /api/settings rejects invalid theme."""
        response = client.patch(
            "/api/settings",
            headers=auth_headers,
            json={"theme": "blue"}
        )
        assert response.status_code == 422  # Validation error

    def test_invalid_language(self, client, auth_headers):
        """PATCH /api/settings rejects invalid language."""
        response = client.patch(
            "/api/settings",
            headers=auth_headers,
            json={"language": "de"}
        )
        assert response.status_code == 422  # Validation error


class TestProfileEndpoints:
    """Test suite for profile update endpoints."""

    def test_update_name(self, client, auth_headers):
        """PATCH /api/settings/profile updates user name."""
        # Get current user info
        me_response = client.get("/api/auth/me", headers=auth_headers)
        assert me_response.status_code == 200
        original_name = me_response.json()["name"]

        # Update name
        new_name = "Test User Updated"
        response = client.patch(
            "/api/settings/profile",
            headers=auth_headers,
            json={"name": new_name}
        )

        assert response.status_code == 200
        assert response.json()["name"] == new_name

        # Restore original name
        client.patch(
            "/api/settings/profile",
            headers=auth_headers,
            json={"name": original_name}
        )

    def test_profile_requires_auth(self, client):
        """Profile endpoints require authentication."""
        response = client.patch(
            "/api/settings/profile",
            json={"name": "Hacker"}
        )
        assert response.status_code in [401, 403]


class TestPasswordEndpoints:
    """Test suite for password change endpoints."""

    def test_password_change_wrong_current(self, client, auth_headers):
        """POST /api/settings/password rejects wrong current password."""
        response = client.post(
            "/api/settings/password",
            headers=auth_headers,
            json={
                "current_password": "wrongpassword123",
                "new_password": "newpassword456",
                "confirm_password": "newpassword456"
            }
        )

        assert response.status_code == 400
        assert "incorrect" in response.json()["detail"].lower()

    def test_password_change_requires_auth(self, client):
        """Password change requires authentication."""
        response = client.post(
            "/api/settings/password",
            json={
                "current_password": "old",
                "new_password": "new",
                "confirm_password": "new"
            }
        )
        assert response.status_code in [401, 403]

    def test_password_change_validation(self, client, auth_headers):
        """POST /api/settings/password validates input."""
        # Missing fields should fail
        response = client.post(
            "/api/settings/password",
            headers=auth_headers,
            json={"current_password": "test"}
        )
        assert response.status_code == 422
