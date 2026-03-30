"""Pytest configuration and fixtures.

Integration tests run against a live backend server.
Make sure the backend is running: docker compose -f docker-compose.dev.yml up -d

Environment variables:
    TEST_API_URL: Backend URL (default: http://localhost:8000)
    TEST_USER_EMAIL / TEST_USER_PASSWORD: Regular user credentials
    TEST_ADMIN_EMAIL / TEST_ADMIN_PASSWORD: Admin user credentials
"""
import os
from uuid import uuid4

import pytest
import httpx


def unique_name(prefix: str = "Test") -> str:
    """Generate a unique name for test data."""
    return f"{prefix}_{uuid4().hex[:8]}"


def random_email() -> str:
    """Generate a unique email for test registration."""
    return f"test_{uuid4().hex[:8]}@testmaptimize.local"

# Test against running backend
BASE_URL = os.environ.get("TEST_API_URL", "http://localhost:8000")

# Test credentials - MUST be set via environment variables (no defaults for security)
TEST_USER_EMAIL = os.environ.get("TEST_USER_EMAIL")
TEST_USER_PASSWORD = os.environ.get("TEST_USER_PASSWORD")
TEST_ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL")
TEST_ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD")


@pytest.fixture(scope="session")
def base_url():
    """Base URL for API requests."""
    return BASE_URL


@pytest.fixture(scope="session")
def auth_token(base_url):
    """Get authentication token for test user.

    Credentials MUST be set via environment variables:
    - TEST_USER_EMAIL: Test user email
    - TEST_USER_PASSWORD: Test user password

    Tests will skip if credentials are not configured.
    """
    if not TEST_USER_EMAIL or not TEST_USER_PASSWORD:
        pytest.skip("Test credentials not configured. Set TEST_USER_EMAIL and TEST_USER_PASSWORD environment variables.")

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


@pytest.fixture(scope="session")
def admin_token(base_url):
    """Get authentication token for admin user.

    Set TEST_ADMIN_EMAIL and TEST_ADMIN_PASSWORD env vars.
    Tests requiring admin will skip if not configured.
    """
    if not TEST_ADMIN_EMAIL or not TEST_ADMIN_PASSWORD:
        pytest.skip("Admin credentials not configured. Set TEST_ADMIN_EMAIL and TEST_ADMIN_PASSWORD.")

    with httpx.Client(base_url=base_url) as client:
        response = client.post(
            "/api/auth/login",
            data={
                "username": TEST_ADMIN_EMAIL,
                "password": TEST_ADMIN_PASSWORD
            }
        )
        if response.status_code != 200:
            pytest.skip(f"Admin login failed: {response.status_code}")
        return response.json()["access_token"]


@pytest.fixture(scope="session")
def admin_headers(admin_token):
    """Get authorization headers for admin user."""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def client(base_url):
    """HTTP client for API requests."""
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client


@pytest.fixture
def test_experiment(client, auth_headers):
    """Create a temporary experiment for testing. Cleaned up after test."""
    response = client.post(
        "/api/experiments",
        headers=auth_headers,
        json={"name": f"_test_experiment_{os.urandom(4).hex()}"}
    )
    assert response.status_code == 201, f"Failed to create test experiment: {response.text}"
    experiment = response.json()

    yield experiment

    # Cleanup
    client.delete(f"/api/experiments/{experiment['id']}", headers=auth_headers)


@pytest.fixture
def test_metric(client, auth_headers):
    """Create a temporary metric for testing. Cleaned up after test."""
    response = client.post(
        "/api/metrics",
        headers=auth_headers,
        json={"name": f"_test_metric_{os.urandom(4).hex()}"}
    )
    assert response.status_code == 201, f"Failed to create test metric: {response.text}"
    metric = response.json()

    yield metric

    # Cleanup
    client.delete(f"/api/metrics/{metric['id']}", headers=auth_headers)


@pytest.fixture
def test_chat_thread(client, auth_headers):
    """Create a temporary chat thread for testing. Cleaned up after test."""
    response = client.post(
        "/api/chat/threads",
        headers=auth_headers,
        json={"name": f"_test_thread_{os.urandom(4).hex()}"}
    )
    assert response.status_code == 201, f"Failed to create test thread: {response.text}"
    thread = response.json()

    yield thread

    # Cleanup
    client.delete(f"/api/chat/threads/{thread['id']}", headers=auth_headers)
