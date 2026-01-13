"""Pytest configuration and fixtures.

Integration tests that verify the ranking undo feature restores
previous mu/sigma values correctly after comparisons.

These tests run against a live backend server.
Make sure the backend is running: docker-compose up -d
"""
import os
import pytest
import httpx

# Test against running backend
BASE_URL = os.environ.get("TEST_API_URL", "http://localhost:8000")

# Test credentials - use environment variables for security
TEST_USER_EMAIL = os.environ.get("TEST_USER_EMAIL", "12bprusek@gym-nymburk.cz")
TEST_USER_PASSWORD = os.environ.get("TEST_USER_PASSWORD", "82c17878")


@pytest.fixture(scope="session")
def base_url():
    """Base URL for API requests."""
    return BASE_URL


@pytest.fixture(scope="session")
def auth_token(base_url):
    """Get authentication token for test user.

    Credentials are loaded from environment variables:
    - TEST_USER_EMAIL: Test user email
    - TEST_USER_PASSWORD: Test user password
    """
    with httpx.Client(base_url=base_url) as client:
        response = client.post(
            "/api/auth/login",
            data={
                "username": TEST_USER_EMAIL,
                "password": TEST_USER_PASSWORD
            }
        )
        assert response.status_code == 200, f"Login failed: {response.text}"
        return response.json()["access_token"]


@pytest.fixture(scope="session")
def auth_headers(auth_token):
    """Get authorization headers."""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def client(base_url):
    """HTTP client for API requests."""
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client
