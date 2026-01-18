import { test as base, Page, BrowserContext } from "@playwright/test";
import path from "path";

/**
 * Custom test fixtures for Maptimize E2E tests.
 *
 * Provides:
 * - authenticatedPage: Pre-authenticated page ready for testing
 * - testExperiment: Creates a test experiment and cleans up after
 */

// Test user info
export const TEST_USER = {
  email: process.env.TEST_USER_EMAIL || "e2e-test@maptimize.test.com",
  password: process.env.TEST_USER_PASSWORD || "testpassword123",
  name: "E2E Test User",
};

// Storage state path
const AUTH_FILE = path.join(__dirname, ".auth/user.json");

// Custom fixture types
type CustomFixtures = {
  authenticatedPage: Page;
  authenticatedContext: BrowserContext;
};

/**
 * Extended test with custom fixtures.
 */
export const test = base.extend<CustomFixtures>({
  // Authenticated browser context
  authenticatedContext: async ({ browser }, use) => {
    const context = await browser.newContext({
      storageState: AUTH_FILE,
    });
    await use(context);
    await context.close();
  },

  // Authenticated page
  authenticatedPage: async ({ authenticatedContext }, use) => {
    const page = await authenticatedContext.newPage();
    await use(page);
    await page.close();
  },
});

export { expect } from "@playwright/test";

/**
 * Helper to get auth token from storage state.
 */
export async function getAuthToken(page: Page): Promise<string | null> {
  return await page.evaluate(() => {
    return localStorage.getItem("token");
  });
}

/**
 * Helper to check if user is logged in.
 */
export async function isLoggedIn(page: Page): Promise<boolean> {
  const token = await getAuthToken(page);
  return token !== null && token.length > 0;
}

/**
 * Helper to logout.
 */
export async function logout(page: Page): Promise<void> {
  await page.evaluate(() => {
    localStorage.removeItem("token");
  });
  await page.goto("/auth");
}

/**
 * Helper to wait for API response.
 */
export async function waitForApiResponse(
  page: Page,
  urlPattern: string | RegExp,
  options: { timeout?: number } = {}
): Promise<void> {
  await page.waitForResponse(
    (response) => {
      const url = response.url();
      if (typeof urlPattern === "string") {
        return url.includes(urlPattern);
      }
      return urlPattern.test(url);
    },
    { timeout: options.timeout || 30_000 }
  );
}
