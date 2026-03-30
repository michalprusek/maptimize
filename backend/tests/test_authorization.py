"""Cross-cutting authorization tests.

Integration tests that verify:
- Admin endpoints reject regular users with 403
- Protected endpoints reject unauthenticated requests with 401
- Invalid/malformed tokens are rejected with 401

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d
"""
import pytest


class TestAdminEndpointProtection:
    """Verify that admin-only endpoints return 403 for regular users."""

    ADMIN_ENDPOINTS = [
        ("GET", "/api/admin/stats"),
        ("GET", "/api/admin/users"),
        ("GET", "/api/admin/gpu/status"),
    ]

    @pytest.mark.parametrize("method,path", ADMIN_ENDPOINTS)
    def test_regular_user_gets_403(self, client, auth_headers, method, path):
        """Regular user accessing admin endpoint receives 403 Forbidden."""
        if method == "GET":
            response = client.get(path, headers=auth_headers)
        else:
            response = client.post(path, headers=auth_headers)
        assert response.status_code == 403


class TestAuthRequired:
    """Verify that protected endpoints return 401 without authentication."""

    PROTECTED_ENDPOINTS = [
        ("GET", "/api/experiments/"),
        ("GET", "/api/settings"),
        ("GET", "/api/chat/threads"),
        ("GET", "/api/rag/documents"),
        ("GET", "/api/embeddings/status"),
        ("GET", "/api/segmentation/capabilities"),
        ("GET", "/api/bug-reports"),
        ("GET", "/api/admin/stats"),
    ]

    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    def test_no_auth_gets_401(self, client, method, path):
        """Request without Authorization header returns 401."""
        if method == "GET":
            response = client.get(path)
        else:
            response = client.post(path)
        assert response.status_code == 401


class TestInvalidToken:
    """Verify that invalid tokens are rejected."""

    def test_garbage_token(self, client):
        """Completely invalid token string returns 401."""
        headers = {"Authorization": "Bearer garbage_not_a_jwt_token"}
        response = client.get("/api/auth/me", headers=headers)
        assert response.status_code == 401

    def test_empty_bearer(self, client):
        """Empty Bearer value returns 401."""
        headers = {"Authorization": "Bearer "}
        response = client.get("/api/auth/me", headers=headers)
        assert response.status_code == 401

    def test_missing_bearer_prefix(self, client):
        """Token without 'Bearer' prefix returns 401 or 403."""
        headers = {"Authorization": "some_token_value"}
        response = client.get("/api/auth/me", headers=headers)
        assert response.status_code in [401, 403]

    def test_malformed_jwt(self, client):
        """Malformed JWT with correct structure but invalid signature returns 401."""
        # A structurally valid JWT with three dot-separated base64 parts but garbage content
        fake_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI5OTk5OTkiLCJleHAiOjE2MDAwMDAwMDB9.invalid_signature"
        headers = {"Authorization": f"Bearer {fake_jwt}"}
        response = client.get("/api/auth/me", headers=headers)
        assert response.status_code == 401


class TestExpiredToken:
    """Verify that expired tokens are rejected."""

    def test_expired_jwt_structure(self, client):
        """A JWT-like token with an obviously past expiration returns 401."""
        # This is a structurally valid JWT with exp=0 (Unix epoch),
        # which any server would reject as expired (even if signature were valid)
        import base64
        import json

        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps({"sub": "99999", "exp": 0}).encode()
        ).rstrip(b"=").decode()
        signature = base64.urlsafe_b64encode(b"fake_sig").rstrip(b"=").decode()

        expired_token = f"{header}.{payload}.{signature}"
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = client.get("/api/auth/me", headers=headers)
        assert response.status_code == 401
