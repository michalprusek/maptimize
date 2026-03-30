"""Tests for authentication endpoints.

Integration tests that verify:
- POST /api/auth/register - user registration
- POST /api/auth/login - user login
- GET /api/auth/me - current user info
- POST /api/auth/refresh - token refresh

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d
"""
import pytest

from conftest import random_email as _random_email


class TestRegistration:
    """Test suite for POST /api/auth/register."""

    def test_register_new_user(self, client):
        """Register a new user returns 201 with token and user info."""
        email = _random_email()
        response = client.post(
            "/api/auth/register",
            json={
                "name": "Test User",
                "email": email,
                "password": "securepass123",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == email
        assert data["user"]["name"] == "Test User"
        assert "id" in data["user"]
        assert "role" in data["user"]

    def test_register_duplicate_email(self, client):
        """Registering with an already-used email returns 400."""
        email = _random_email()
        payload = {
            "name": "First User",
            "email": email,
            "password": "securepass123",
        }

        # First registration succeeds
        first = client.post("/api/auth/register", json=payload)
        assert first.status_code == 201

        # Second registration with same email fails
        response = client.post("/api/auth/register", json=payload)
        assert response.status_code == 400
        assert "already registered" in response.json()["detail"].lower()

    def test_register_invalid_email(self, client):
        """Registering with an invalid email returns 422."""
        response = client.post(
            "/api/auth/register",
            json={
                "name": "Bad Email",
                "email": "not-an-email",
                "password": "securepass123",
            },
        )
        assert response.status_code == 422

    def test_register_short_password(self, client):
        """Registering with a password shorter than 8 chars returns 422."""
        response = client.post(
            "/api/auth/register",
            json={
                "name": "Short Pass",
                "email": _random_email(),
                "password": "short",
            },
        )
        assert response.status_code == 422

    def test_register_short_name(self, client):
        """Registering with a name shorter than 2 chars returns 422."""
        response = client.post(
            "/api/auth/register",
            json={
                "name": "X",
                "email": _random_email(),
                "password": "securepass123",
            },
        )
        assert response.status_code == 422

    def test_register_missing_fields(self, client):
        """Registering with missing required fields returns 422."""
        # Missing password
        response = client.post(
            "/api/auth/register",
            json={"name": "No Password", "email": _random_email()},
        )
        assert response.status_code == 422

        # Missing name
        response = client.post(
            "/api/auth/register",
            json={"email": _random_email(), "password": "securepass123"},
        )
        assert response.status_code == 422

        # Missing email
        response = client.post(
            "/api/auth/register",
            json={"name": "No Email", "password": "securepass123"},
        )
        assert response.status_code == 422

    def test_register_returns_valid_jwt(self, client):
        """The token returned from registration can authenticate requests."""
        response = client.post(
            "/api/auth/register",
            json={
                "name": "JWT Test",
                "email": _random_email(),
                "password": "securepass123",
            },
        )
        assert response.status_code == 201
        token = response.json()["access_token"]

        # Use the token to call /me
        me_response = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_response.status_code == 200
        assert me_response.json()["name"] == "JWT Test"

    def test_register_provisions_template_data(self, client):
        """Newly registered user receives provisioned template experiments."""
        response = client.post(
            "/api/auth/register",
            json={
                "name": "Provisioned User",
                "email": _random_email(),
                "password": "securepass123",
            },
        )
        assert response.status_code == 201
        token = response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # The provisioning should copy template data (experiments from user 1)
        experiments = client.get("/api/experiments", headers=headers)
        assert experiments.status_code == 200
        assert isinstance(experiments.json(), list)
        assert len(experiments.json()) > 0, "Provisioned user should have template experiments"


class TestLogin:
    """Test suite for POST /api/auth/login."""

    def test_login_valid_credentials(self, client, auth_headers):
        """Login with valid credentials returns 200 and a token.

        Uses the session-level auth fixture to confirm the test user exists,
        then re-logs in explicitly.
        """
        import os

        email = os.environ.get("TEST_USER_EMAIL")
        password = os.environ.get("TEST_USER_PASSWORD")
        if not email or not password:
            pytest.skip("Test credentials not configured")

        response = client.post(
            "/api/auth/login",
            data={"username": email, "password": password},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == email

    def test_login_wrong_password(self, client):
        """Login with wrong password returns 401."""
        # Register a user so we know the email exists
        email = _random_email()
        client.post(
            "/api/auth/register",
            json={
                "name": "Wrong Pass",
                "email": email,
                "password": "correctpassword",
            },
        )

        response = client.post(
            "/api/auth/login",
            data={"username": email, "password": "wrongpassword"},
        )
        assert response.status_code == 401

    def test_login_nonexistent_email(self, client):
        """Login with a nonexistent email returns 401."""
        response = client.post(
            "/api/auth/login",
            data={
                "username": f"nonexistent_{uuid4().hex[:8]}@testmaptimize.local",
                "password": "anypassword",
            },
        )
        assert response.status_code == 401

    def test_login_returns_user_role(self, client):
        """Login response includes the user role."""
        email = _random_email()
        client.post(
            "/api/auth/register",
            json={
                "name": "Role Test",
                "email": email,
                "password": "securepass123",
            },
        )

        response = client.post(
            "/api/auth/login",
            data={"username": email, "password": "securepass123"},
        )
        assert response.status_code == 200
        assert "role" in response.json()["user"]


class TestMe:
    """Test suite for GET /api/auth/me."""

    def test_me_returns_user(self, client, auth_headers):
        """GET /me returns current user information."""
        response = client.get("/api/auth/me", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert "email" in data
        assert "name" in data
        assert "role" in data

    def test_me_requires_auth(self, client):
        """GET /me without authorization header returns 401."""
        response = client.get("/api/auth/me")
        assert response.status_code == 401

    def test_me_invalid_token(self, client):
        """GET /me with an invalid token returns 401."""
        response = client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert response.status_code == 401


class TestRefreshToken:
    """Test suite for POST /api/auth/refresh."""

    def test_refresh_returns_new_token(self, client, auth_headers):
        """POST /refresh returns a new access token."""
        response = client.post("/api/auth/refresh", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "user" in data

    def test_refresh_requires_auth(self, client):
        """POST /refresh without authorization returns 401."""
        response = client.post("/api/auth/refresh")
        assert response.status_code == 401

    def test_refreshed_token_works(self, client, auth_headers):
        """The refreshed token can be used for subsequent requests."""
        # Get a new token
        refresh_response = client.post("/api/auth/refresh", headers=auth_headers)
        assert refresh_response.status_code == 200
        new_token = refresh_response.json()["access_token"]

        # Use the new token to call /me
        me_response = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert me_response.status_code == 200
        assert "id" in me_response.json()
