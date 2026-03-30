"""Tests for bug report endpoints.

Integration tests that verify:
- POST /api/bug-reports - create a bug report
- GET /api/bug-reports - list user's bug reports
- GET /api/bug-reports/all - list all bug reports (admin only)

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d
"""
import pytest


class TestBugReportCreate:
    """Test suite for POST /api/bug-reports."""

    def test_create_requires_auth(self, client):
        """Creating a bug report without auth returns 401."""
        response = client.post(
            "/api/bug-reports",
            json={"description": "Something is broken in the app"},
        )
        assert response.status_code == 401

    def test_create_valid_report(self, client, auth_headers):
        """Creating a bug report with valid description returns 201."""
        response = client.post(
            "/api/bug-reports",
            headers=auth_headers,
            json={"description": "The image upload fails when file is larger than 50MB"},
        )
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["description"] is not None
        assert "created_at" in data
        assert "status" in data

    def test_create_short_description(self, client, auth_headers):
        """Creating a bug report with description shorter than 10 chars returns 422."""
        response = client.post(
            "/api/bug-reports",
            headers=auth_headers,
            json={"description": "short"},
        )
        assert response.status_code == 422

    def test_create_with_all_fields(self, client, auth_headers):
        """Creating a bug report with all optional fields succeeds."""
        response = client.post(
            "/api/bug-reports",
            headers=auth_headers,
            json={
                "description": "Image upload page crashes when clicking submit button",
                "category": "bug",
                "browser_info": "Chrome 120.0.6099.130",
                "page_url": "/experiments/1/images",
                "screen_resolution": "1920x1080",
                "user_settings_json": '{"theme":"dark","language":"en"}',
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["browser_info"] == "Chrome 120.0.6099.130"
        assert data["page_url"] == "/experiments/1/images"
        assert data["screen_resolution"] == "1920x1080"

    def test_create_empty_body(self, client, auth_headers):
        """Creating a bug report with empty body returns 422."""
        response = client.post(
            "/api/bug-reports",
            headers=auth_headers,
            json={},
        )
        assert response.status_code == 422

    def test_create_missing_description(self, client, auth_headers):
        """Creating a bug report without description field returns 422."""
        response = client.post(
            "/api/bug-reports",
            headers=auth_headers,
            json={"browser_info": "Chrome"},
        )
        assert response.status_code == 422

    def test_create_xss_sanitization(self, client, auth_headers):
        """Bug report description with HTML is sanitized."""
        response = client.post(
            "/api/bug-reports",
            headers=auth_headers,
            json={
                "description": '<script>alert("xss")</script> This is a real bug report'
            },
        )
        assert response.status_code == 201
        data = response.json()
        # HTML entities should be escaped
        assert "<script>" not in data["description"]


class TestBugReportList:
    """Test suite for GET /api/bug-reports."""

    def test_list_requires_auth(self, client):
        """Listing bug reports without auth returns 401."""
        response = client.get("/api/bug-reports")
        assert response.status_code == 401

    def test_list_own_reports(self, client, auth_headers):
        """Listing bug reports returns the user's reports."""
        response = client.get("/api/bug-reports", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "reports" in data
        assert "total" in data
        assert isinstance(data["reports"], list)
        assert data["total"] >= 0


class TestBugReportAdminList:
    """Test suite for GET /api/bug-reports/all."""

    def test_admin_list_requires_auth(self, client):
        """Listing all bug reports without auth returns 401."""
        response = client.get("/api/bug-reports/all")
        assert response.status_code == 401

    def test_admin_list_forbidden_for_regular_user(self, client, auth_headers):
        """Regular user cannot access all bug reports -> 403."""
        response = client.get("/api/bug-reports/all", headers=auth_headers)
        assert response.status_code == 403
