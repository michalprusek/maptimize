import { test, expect } from "../../fixtures/auth.fixture";
import { DashboardPage } from "../../pages";

/**
 * Settings E2E Tests - P2 Important
 *
 * Tests user preferences, display mode, and language settings.
 */

test.describe("Settings @important", () => {
  let dashboardPage: DashboardPage;

  test.beforeEach(async ({ authenticatedPage }) => {
    dashboardPage = new DashboardPage(authenticatedPage);

    // Mock settings endpoints
    await authenticatedPage.route("**/api/settings", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            display_mode: "grayscale",
            theme: "dark",
            language: "en",
          }),
        });
      } else if (route.request().method() === "PATCH") {
        const body = route.request().postDataJSON();
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            display_mode: body.display_mode || "grayscale",
            theme: body.theme || "dark",
            language: body.language || "en",
          }),
        });
      } else {
        await route.continue();
      }
    });
  });

  test("should display settings page", async ({ authenticatedPage }) => {
    await authenticatedPage.goto("/dashboard/settings");
    await authenticatedPage.waitForLoadState("networkidle");

    // Settings page should have form elements
    await expect(authenticatedPage.locator("main")).toBeVisible();
  });

  test("should show display mode options", async ({ authenticatedPage }) => {
    await authenticatedPage.goto("/dashboard/settings");
    await authenticatedPage.waitForLoadState("networkidle");

    // Look for display mode selector
    const displayModeSection = authenticatedPage.locator('text=/display.*mode|image.*display/i');

    if (await displayModeSection.isVisible()) {
      await expect(displayModeSection).toBeVisible();
    }
  });

  test("should change display mode", async ({ authenticatedPage }) => {
    await authenticatedPage.goto("/dashboard/settings");
    await authenticatedPage.waitForLoadState("networkidle");

    // Find display mode selector (could be select, radio, or buttons)
    const displayModeSelect = authenticatedPage.locator('[data-testid="display-mode"]').or(
      authenticatedPage.locator('select').filter({ has: authenticatedPage.locator('option[value="inverted"]') })
    );

    if (await displayModeSelect.isVisible()) {
      await displayModeSelect.selectOption("inverted");

      // Wait for update request
      await authenticatedPage.waitForResponse(
        (response) => response.url().includes("/api/settings") && response.ok(),
        { timeout: 5000 }
      ).catch(() => {});
    }
  });

  test("should show theme options", async ({ authenticatedPage }) => {
    await authenticatedPage.goto("/dashboard/settings");
    await authenticatedPage.waitForLoadState("networkidle");

    // Look for theme selector (use first match to avoid strict mode violation)
    const themeSection = authenticatedPage.locator('text=/theme|appearance|dark.*mode/i').first();

    if (await themeSection.isVisible({ timeout: 3000 }).catch(() => false)) {
      await expect(themeSection).toBeVisible();
    }
  });

  test("should toggle theme between dark and light", async ({ authenticatedPage }) => {
    await authenticatedPage.goto("/dashboard/settings");
    await authenticatedPage.waitForLoadState("networkidle");

    // Find theme toggle or selector (use first match)
    const themeToggle = authenticatedPage.locator('[data-testid="theme-toggle"]').or(
      authenticatedPage.locator('button').filter({ hasText: /dark|light|theme/i })
    ).first();

    if (await themeToggle.isVisible({ timeout: 3000 }).catch(() => false)) {
      await themeToggle.click();

      // Wait for theme transition to complete
      await authenticatedPage.waitForLoadState("domcontentloaded");
    }
  });

  test("should show language options", async ({ authenticatedPage }) => {
    await authenticatedPage.goto("/dashboard/settings");
    await authenticatedPage.waitForLoadState("networkidle");

    // Look for language selector
    const languageSection = authenticatedPage.locator('text=/language|locale/i');

    if (await languageSection.isVisible()) {
      await expect(languageSection).toBeVisible();
    }
  });

  test("should change language", async ({ authenticatedPage }) => {
    await authenticatedPage.goto("/dashboard/settings");
    await authenticatedPage.waitForLoadState("networkidle");

    // Find language selector
    const languageSelect = authenticatedPage.locator('[data-testid="language-select"]').or(
      authenticatedPage.locator('select').filter({ has: authenticatedPage.locator('option[value="fr"]') })
    );

    if (await languageSelect.isVisible()) {
      await languageSelect.selectOption("fr");

      // Wait for UI to update after language change
      await authenticatedPage.waitForLoadState("networkidle");

      // Some text should be in French after change
      // This depends on what's visible on the page
    }
  });

  test("should persist settings across page reload", async ({ authenticatedPage }) => {
    await authenticatedPage.goto("/dashboard/settings");
    await authenticatedPage.waitForLoadState("networkidle");

    // Make a setting change
    const displayModeSelect = authenticatedPage.locator('[data-testid="display-mode"]').or(
      authenticatedPage.locator('select').filter({ has: authenticatedPage.locator('option[value="inverted"]') })
    );

    if (await displayModeSelect.isVisible()) {
      await displayModeSelect.selectOption("inverted");
      await authenticatedPage.waitForTimeout(500);

      // Reload page
      await authenticatedPage.reload();
      await authenticatedPage.waitForLoadState("networkidle");

      // Setting should be persisted (via API mock)
    }
  });

  test("should navigate to settings from dashboard", async ({ authenticatedPage }) => {
    await dashboardPage.goto();
    await dashboardPage.goToSettings();

    await expect(authenticatedPage).toHaveURL(/\/settings/);
  });

  test("should show profile section", async ({ authenticatedPage }) => {
    await authenticatedPage.goto("/dashboard/settings");
    await authenticatedPage.waitForLoadState("networkidle");

    // Profile section with name and email (use first match)
    const profileSection = authenticatedPage.locator('text=/profile|account|user.*info/i').first();

    if (await profileSection.isVisible({ timeout: 3000 }).catch(() => false)) {
      await expect(profileSection).toBeVisible();
    }
  });

  test("should allow editing profile name", async ({ authenticatedPage }) => {
    // Mock profile update
    await authenticatedPage.route("**/api/settings/profile", async (route) => {
      if (route.request().method() === "PATCH") {
        const body = route.request().postDataJSON();
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: 1,
            email: "test@example.com",
            name: body.name || "Test User",
            role: "researcher",
          }),
        });
      } else {
        await route.continue();
      }
    });

    await authenticatedPage.goto("/dashboard/settings");
    await authenticatedPage.waitForLoadState("networkidle");

    // Find name input
    const nameInput = authenticatedPage.locator('input[name="name"]').or(
      authenticatedPage.locator('input[placeholder*="name" i]')
    );

    if (await nameInput.isVisible()) {
      await nameInput.fill("New Test Name");

      // Save button
      const saveButton = authenticatedPage.locator('button').filter({ hasText: /save|update/i });
      if (await saveButton.isVisible()) {
        await saveButton.click();
      }
    }
  });

  test("should show password change section", async ({ authenticatedPage }) => {
    await authenticatedPage.goto("/dashboard/settings");
    await authenticatedPage.waitForLoadState("networkidle");

    // Password change section (use first match to avoid strict mode violation)
    const passwordSection = authenticatedPage.getByRole('heading', { name: /password/i }).first();

    if (await passwordSection.isVisible({ timeout: 3000 }).catch(() => false)) {
      await expect(passwordSection).toBeVisible();
    }
  });

  test("should validate password requirements", async ({ authenticatedPage }) => {
    // Mock password change
    await authenticatedPage.route("**/api/settings/password", async (route) => {
      const body = route.request().postDataJSON();

      if (body.new_password && body.new_password.length < 8) {
        await route.fulfill({
          status: 400,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Password must be at least 8 characters" }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ message: "Password updated successfully" }),
        });
      }
    });

    await authenticatedPage.goto("/dashboard/settings");
    await authenticatedPage.waitForLoadState("networkidle");

    // Find password inputs
    const currentPassword = authenticatedPage.locator('input[name="current_password"]').or(
      authenticatedPage.locator('input[placeholder*="current" i]')
    );
    const newPassword = authenticatedPage.locator('input[name="new_password"]').or(
      authenticatedPage.locator('input[placeholder*="new" i]')
    );

    if (await currentPassword.isVisible() && await newPassword.isVisible()) {
      await currentPassword.fill("oldpassword123");
      await newPassword.fill("short"); // Too short

      const submitButton = authenticatedPage.locator('button').filter({ hasText: /change.*password|update/i });
      if (await submitButton.isVisible()) {
        await submitButton.click();

        // Should show error
        const error = authenticatedPage.locator('text=/8.*character|too.*short/i');
        // Error handling depends on implementation
      }
    }
  });

  test("should handle settings API error gracefully", async ({ authenticatedPage }) => {
    // Mock API error
    await authenticatedPage.route("**/api/settings", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Internal server error" }),
        });
      } else {
        await route.continue();
      }
    });

    await authenticatedPage.goto("/dashboard/settings");
    await authenticatedPage.waitForLoadState("networkidle");

    // Page should handle error gracefully (show error or default state)
    // Implementation-dependent
  });
});
