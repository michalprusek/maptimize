"""Pytest configuration and fixtures."""
import pytest
import httpx

# Test against running backend
BASE_URL = "http://localhost:8000"


@pytest.fixture(scope="session")
def base_url():
    """Base URL for API requests."""
    return BASE_URL


@pytest.fixture(scope="session")
def auth_token(base_url):
    """Get authentication token for default test user."""
    with httpx.Client(base_url=base_url) as client:
        response = client.post(
            "/api/auth/login",
            data={
                "username": "12bprusek@gym-nymburk.cz",
                "password": "82c17878"
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
