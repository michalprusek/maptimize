# Testing Patterns

**Analysis Date:** 2026-01-29

## Test Framework

**Runner (Frontend):**
- Playwright `@playwright/test` v1.50.0
- Config: `frontend/e2e/playwright.config.ts`
- Test directory: `frontend/e2e/tests/`

**Runner (Backend):**
- pytest v7.4.0 with pytest-asyncio v0.23.0
- Config: `backend/pytest.ini`
- Test directory: `backend/tests/`

**Assertion Library:**
- Frontend: Playwright built-in expect assertions
- Backend: Standard assert statements with pytest

**Run Commands:**
```bash
# Frontend E2E
npm run test:e2e              # Run all tests
npm run test:e2e:critical     # Run only @critical tagged tests
npm run test:e2e:ui           # Interactive UI mode for debugging
npm run test:e2e:debug        # Debug mode with inspector
npm run test:e2e:report       # View HTML test report

# Backend tests
pytest                        # Run all tests
pytest -v                     # Verbose output
pytest backend/tests/test_file.py -k test_name  # Run specific test
pytest --collect-only         # List all tests without running
```

## Test File Organization

**Frontend Location:**
- Path pattern: `frontend/e2e/tests/{feature}/{test_name}.spec.ts`
- Examples:
  - `frontend/e2e/tests/auth/login.spec.ts` - Login tests
  - `frontend/e2e/tests/experiments/crud.spec.ts` - Experiment CRUD
  - `frontend/e2e/tests/images/upload.spec.ts` - Image upload
  - `frontend/e2e/tests/ranking/comparison.spec.ts` - Ranking comparisons
  - `frontend/e2e/tests/editor/navigation.spec.ts` - Editor navigation
  - `frontend/e2e/tests/settings/preferences.spec.ts` - User settings

**Backend Location:**
- Path pattern: `backend/tests/test_{feature}.py`
- Examples:
  - `backend/tests/test_image_workflow.py` - Image upload workflow
  - `backend/tests/test_ranking_undo.py` - Ranking undo feature
  - `backend/tests/test_sam_coordinates.py` - SAM segmentation
  - `backend/tests/test_settings.py` - User settings

**Naming (Frontend):**
- Files: `{feature}.spec.ts`
- Describe blocks: `test.describe("Feature Name @tag", () => { ... })`
- Test cases: `test("should do something", async ({ page }) => { ... })`

**Naming (Backend):**
- Files: `test_{feature}.py`
- Classes: `class Test{Feature}:` (Test prefix required by pytest.ini)
- Methods: `def test_{scenario}(self, fixture):`

## Test Structure

**Frontend (Playwright):**
```typescript
import { test, expect } from "@playwright/test";
import { Page } from "@playwright/test";

test.describe("Authentication @critical", () => {
  // Disable auth state for auth tests
  test.use({ storageState: { cookies: [], origins: [] } });

  let authPage: AuthPage;

  test.beforeEach(async ({ page }) => {
    authPage = new AuthPage(page);
  });

  test("should display login form", async ({ page }) => {
    await authPage.goto();
    await expect(authPage.emailInput).toBeVisible();
  });

  test.afterEach(async ({ page }) => {
    // Cleanup if needed
  });
});
```

**Backend (pytest):**
```python
import pytest
import httpx

class TestUploadEndpoint:
    """Tests for /api/images/upload endpoint."""

    def test_upload_requires_authentication(self, client):
        """Test that upload endpoint requires authentication."""
        fake_image = io.BytesIO(b"fake image content")
        response = client.post(
            "/api/images/upload",
            data={"experiment_id": "1"},
            files={"file": ("test.png", fake_image, "image/png")}
        )
        assert response.status_code == 401

    def test_upload_with_valid_data(self, client, auth_headers):
        """Test successful upload with authentication."""
        # Create fixture data
        response = client.post(
            "/api/images/upload",
            headers=auth_headers,
            data={"experiment_id": "1"},
            files={"file": ("test.png", fake_image, "image/png")}
        )
        assert response.status_code == 201
```

**Patterns:**
- Page Object Model for frontend (encapsulates selectors and actions)
- Setup: `test.beforeEach()` for common initialization
- Cleanup: `test.afterEach()` for resource cleanup
- Fixtures: Dependency injection for test data and auth
- Async/await for async operations
- Descriptive test names explaining the scenario

## Mocking

**Frontend (Playwright):**
- Network mocking: `page.route("**/api/endpoint", (route) => route.abort())`
- Error injection: Return error responses or abort requests
- Example from `login.spec.ts`:
```typescript
// Mock network failure
await page.route("**/api/auth/login", (route) => route.abort());
await authPage.login("test@example.com", "password123");
await authPage.expectError(/network|connect|server/i);
```

**Backend (pytest):**
- No explicit mocking framework detected
- Uses real API client (`httpx.Client`)
- Uses real database against running backend
- Can skip tests if fixtures unavailable: `pytest.skip("No experiments available")`

**What to Mock:**
- Frontend: Network failures, API errors, slow responses, missing data
- Backend: Skip tests if database fixtures missing; don't mock—use real API calls

**What NOT to Mock:**
- Frontend: Page navigation, user interactions (use real browser)
- Backend: Database calls (tests use real database)
- Auth flow: Don't mock authentication, use real test credentials

## Fixtures and Factories

**Frontend Test Data:**
- Location: `frontend/e2e/fixtures/`
- Auth state: Created by global setup in `global-setup.ts`
- Stored at: `frontend/e2e/fixtures/.auth/user.json`
- Usage: Auto-loaded via `storageState: "./fixtures/.auth/user.json"` in config

**Backend Fixtures:**
- Location: `backend/tests/conftest.py`
- Session-scoped: `@pytest.fixture(scope="session")`
- Function-scoped: `@pytest.fixture`

**Fixture Examples (Backend):**
```python
@pytest.fixture(scope="session")
def auth_token(base_url):
    """Get authentication token via login."""
    with httpx.Client(base_url=base_url) as client:
        response = client.post("/api/auth/login", data={...})
        return response.json()["access_token"]

@pytest.fixture
def auth_headers(auth_token):
    """Authorization headers for authenticated requests."""
    return {"Authorization": f"Bearer {auth_token}"}

@pytest.fixture
def client(base_url):
    """HTTP client for API requests."""
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client
```

**Environment Configuration:**
- Backend tests: Set via environment variables
  - `TEST_API_URL` - Backend URL (default: http://localhost:8000)
  - `TEST_USER_EMAIL` - Test user email (required, no default)
  - `TEST_USER_PASSWORD` - Test user password (required, no default)
- Frontend tests: Uses `BASE_URL` env var (default: http://localhost:3000)

## Coverage

**Requirements:** No coverage requirements detected in pytest.ini

**View Coverage:**
- Frontend: No coverage tool configured
- Backend: No coverage tool configured

## Test Types

**Unit Tests:**
- Scope: Individual functions and utilities
- Approach: Not primary focus; most tests are integration tests
- Example: Canvas geometry functions in editor

**Integration Tests:**
- Scope: API endpoints with authentication and database
- Approach: Real backend running via Docker
- Example: `test_image_workflow.py` tests Phase 1 upload + Phase 2 batch process
- Example: `login.spec.ts` tests full login flow including redirects

**E2E Tests:**
- Framework: Playwright
- Scope: User journeys across multiple pages
- Approach: Real browser (Chromium), real API, authenticated sessions
- Examples:
  - Auth flow: login → redirect to dashboard → logout
  - Experiment workflow: create → upload images → process → view results
  - Ranking: submit comparisons → view leaderboard

## Common Patterns

**Async Testing (Frontend):**
```typescript
test("should perform async operation", async ({ page }) => {
  // All operations are async
  await authPage.goto();
  await authPage.fillEmail("test@example.com");
  await authPage.submit();

  // Wait for expected state
  await expect(page).toHaveURL(/\/dashboard/);
  await expect(element).toBeVisible();
});
```

**Async Testing (Backend):**
```python
def test_async_operation(client, auth_headers):
    """Test async API operation."""
    response = client.post(
        "/api/images/batch-process",
        headers=auth_headers,
        json={"image_ids": [1, 2, 3], "detect_cells": True}
    )
    assert response.status_code == 200
    assert response.json()["processing_count"] > 0
```

**Error Testing (Frontend):**
```typescript
test("should show error for invalid input", async ({ page }) => {
  await authPage.goto();
  await authPage.fillEmail("invalid-email");
  await authPage.submit();

  // HTML5 validation prevents submission
  await expect(page).toHaveURL(/\/auth/);
});

test("should handle network errors", async ({ page }) => {
  // Mock network failure
  await page.route("**/api/auth/login", (route) => route.abort());
  await authPage.login("test@example.com", "password123");

  // Check error message
  await authPage.expectError(/network|connect|server/i);
});
```

**Error Testing (Backend):**
```python
def test_upload_rejects_invalid_file(client, auth_headers):
    """Test upload rejects invalid file types."""
    fake_file = io.BytesIO(b"not a real image")
    response = client.post(
        "/api/images/upload",
        headers=auth_headers,
        data={"experiment_id": "1"},
        files={"file": ("malware.exe", fake_file, "application/octet-stream")}
    )
    assert response.status_code == 400
    assert "invalid file" in response.json().get("detail", "").lower()
```

**Test Tagging (Frontend):**
- `@critical` - Essential features tested before every commit
  - Login, logout, CRUD operations
  - Run with: `npm run test:e2e:critical`
- `@mobile` - Mobile viewport tests (optional)
  - Only Playwright mobile project runs these tests
- No tag - Regular tests (nice-to-have)

**Test Configuration (Frontend):**
- Timeout: 120 seconds per test (2 minutes)
- Expect timeout: 10 seconds for assertions
- Retries: 2 in CI (flaky network), 0 locally
- Workers: 2 in CI, unlimited locally
- Storage state: Auto-loaded from auth fixture (all tests except auth tests)

**Playwright Fixtures (Frontend):**
- `page` - Browser page object
- `context` - Browser context (optional)
- All tests auto-configure storageState unless overridden

## Workflow Integration

**When to Run Tests:**
| Change | Command |
|--------|---------|
| New page/route | `npm run test:e2e -- e2e/tests/[feature]/` |
| Form changes | `npm run test:e2e -- e2e/tests/[feature]/` |
| API endpoint | `npm run test:e2e -- --grep "[endpoint]"` |
| Auth changes | `npm run test:e2e -- e2e/tests/auth/` |
| Before commit | `npm run test:e2e:critical` (frontend) + `pytest backend/tests/` (backend) |
| Before PR | `npm run test:e2e` (all E2E) + `pytest` (all backend) |

**Skip Tests When:**
- Documentation-only changes
- Config-only changes (without runtime impact)
- Backend-only changes (covered by pytest)

---

*Testing analysis: 2026-01-29*
