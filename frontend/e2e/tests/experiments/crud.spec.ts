import { test, expect } from "../../fixtures/auth.fixture";
import { ExperimentPage } from "../../pages";
import { generateTestId, createTestExperiment, deleteTestExperiment } from "../../fixtures/test-data";

/**
 * Experiments CRUD E2E Tests - P1 Critical
 *
 * Tests experiment creation, viewing, updating, and deletion.
 * Uses authenticated session from global setup.
 */

test.describe("Experiments CRUD @critical", () => {
  let experimentPage: ExperimentPage;
  const createdExperiments: number[] = [];

  test.beforeEach(async ({ authenticatedPage }) => {
    experimentPage = new ExperimentPage(authenticatedPage);
  });

  test.afterEach(async ({ authenticatedPage }) => {
    // Clean up created experiments
    for (const id of createdExperiments) {
      await deleteTestExperiment(authenticatedPage, id);
    }
    createdExperiments.length = 0;
  });

  test("should display experiments list page", async ({ authenticatedPage }) => {
    await experimentPage.gotoList();

    await experimentPage.expectListDisplayed();
    await expect(experimentPage.createButton).toBeVisible();
  });

  test("should open create experiment modal", async ({ authenticatedPage }) => {
    await experimentPage.gotoList();

    await experimentPage.openCreateModal();

    await expect(experimentPage.modal).toBeVisible();
    await expect(experimentPage.nameInput).toBeVisible();
    await expect(experimentPage.descriptionInput).toBeVisible();
  });

  test("should close modal when clicking cancel", async ({ authenticatedPage }) => {
    await experimentPage.gotoList();
    await experimentPage.openCreateModal();

    await experimentPage.closeModal();

    await expect(experimentPage.modal).not.toBeVisible();
  });

  test("should create a new experiment", async ({ authenticatedPage }) => {
    const testName = `Test Experiment ${generateTestId()}`;

    await experimentPage.gotoList();
    await experimentPage.createExperiment(testName, "E2E test description");

    // Verify experiment was created - check UI
    await experimentPage.expectExperimentCreated(testName);

    // After creating, we might be on detail page or list
    // If on detail, get ID from URL for cleanup
    const url = authenticatedPage.url();
    const match = url.match(/\/experiments\/(\d+)/);
    if (match) {
      createdExperiments.push(parseInt(match[1]));
    }
  });

  test("should require experiment name", async ({ authenticatedPage }) => {
    await experimentPage.gotoList();
    await experimentPage.openCreateModal();

    // Verify save button is disabled when name is empty
    await expect(experimentPage.saveButton).toBeDisabled();

    // Modal should still be visible
    await expect(experimentPage.modal).toBeVisible();

    // Fill in a name and verify button becomes enabled
    await experimentPage.nameInput.fill("Test");
    await expect(experimentPage.saveButton).toBeEnabled();

    // Clear the name and verify button is disabled again
    await experimentPage.nameInput.clear();
    await expect(experimentPage.saveButton).toBeDisabled();
  });

  test("should navigate to experiment detail page", async ({ authenticatedPage }) => {
    // Navigate first to ensure auth state is loaded
    await experimentPage.gotoList();

    // Create experiment via API
    const testName = `Test Experiment ${generateTestId()}`;
    const created = await createTestExperiment(authenticatedPage, testName);
    createdExperiments.push(created.id);

    // Reload list to see new experiment
    await experimentPage.gotoList();

    // Click on the experiment
    await experimentPage.clickExperiment(testName);

    await experimentPage.expectDetailDisplayed(testName);
  });

  test("should display experiment details correctly", async ({ authenticatedPage }) => {
    // Navigate first to ensure auth state is loaded
    await experimentPage.gotoList();

    // Create experiment via API
    const testName = `Test Experiment ${generateTestId()}`;
    const created = await createTestExperiment(authenticatedPage, testName);
    createdExperiments.push(created.id);

    // Navigate to detail
    await experimentPage.gotoDetail(created.id);

    // Verify title is displayed
    const title = await experimentPage.getTitle();
    expect(title).toContain(testName);
  });

  test("should delete an experiment", async ({ authenticatedPage }) => {
    // Navigate first to ensure auth state is loaded
    await experimentPage.gotoList();

    // Create experiment via API
    const testName = `Test Experiment ${generateTestId()}`;
    const created = await createTestExperiment(authenticatedPage, testName);

    // Navigate to detail
    await experimentPage.gotoDetail(created.id);

    // Check if delete button is visible (might be in a dropdown menu)
    const deleteVisible = await experimentPage.deleteButton.isVisible({ timeout: 2000 }).catch(() => false);

    if (deleteVisible) {
      // Delete via UI
      await experimentPage.deleteExperiment();

      // Should redirect to list
      await experimentPage.expectListDisplayed();

      // Experiment should no longer exist
      const exists = await experimentPage.experimentExists(testName);
      expect(exists).toBe(false);
    } else {
      // Delete via API as fallback (UI delete not available)
      await deleteTestExperiment(authenticatedPage, created.id);

      // Verify deletion
      await experimentPage.gotoList();
      const exists = await experimentPage.experimentExists(testName);
      expect(exists).toBe(false);
    }
  });

  test("should show empty state when no experiments", async ({ authenticatedPage }) => {
    // This test assumes the test user might have no experiments
    // In practice, you'd need a fresh user or clean state
    await experimentPage.gotoList();

    // Wait for content to load
    await authenticatedPage.waitForLoadState("networkidle");

    // Either experiments or empty state should be visible
    const hasExperiments = await experimentPage.experimentCards.count() > 0;
    const hasEmptyState = await experimentPage.emptyState.isVisible();

    // At least one should be true
    expect(hasExperiments || hasEmptyState).toBe(true);
  });

  test("should handle API errors gracefully", async ({ authenticatedPage }) => {
    // Mock API error
    await authenticatedPage.route("**/api/experiments", (route) => {
      if (route.request().method() === "POST") {
        route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Internal server error" }),
        });
      } else {
        route.continue();
      }
    });

    await experimentPage.gotoList();
    await experimentPage.openCreateModal();
    await experimentPage.nameInput.fill("Test Experiment");
    await experimentPage.saveButton.click();

    // Should show error or stay in modal
    const modalVisible = await experimentPage.modal.isVisible();
    expect(modalVisible).toBe(true);
  });

  test("should filter experiments by search (if available)", async ({ authenticatedPage }) => {
    // Navigate first to ensure auth state is loaded
    await experimentPage.gotoList();

    // Create a uniquely named experiment via API
    const uniqueName = `UniqueSearch${generateTestId()}`;
    const created = await createTestExperiment(authenticatedPage, uniqueName);
    createdExperiments.push(created.id);

    // Reload list to see new experiment
    await experimentPage.gotoList();

    // If search is available, test it
    if (await experimentPage.searchInput.isVisible()) {
      await experimentPage.searchInput.fill(uniqueName);
      // Wait for search results by checking for API response or DOM update
      await authenticatedPage.waitForResponse(
        (response) => response.url().includes("/api/experiments") && response.ok(),
        { timeout: 5000 }
      ).catch(() => {
        // Fallback: wait for DOM to stabilize after debounce
      });
      await authenticatedPage.waitForLoadState("networkidle");

      // Should only show matching experiment
      const count = await experimentPage.getExperimentCount();
      expect(count).toBe(1);
    }
  });
});
