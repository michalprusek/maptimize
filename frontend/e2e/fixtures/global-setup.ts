import { chromium, FullConfig } from "@playwright/test";
import fs from "fs";
import path from "path";
import { TEST_USER, AUTH_FILE } from "./auth.fixture";

/**
 * Global setup - runs once before all tests.
 *
 * Creates authenticated session and saves it to storage state file.
 * All tests will use this pre-authenticated state.
 */

async function globalSetup(config: FullConfig) {
  const { baseURL } = config.projects[0].use;

  // Ensure auth directory exists
  const authDir = path.dirname(AUTH_FILE);
  if (!fs.existsSync(authDir)) {
    fs.mkdirSync(authDir, { recursive: true });
  }

  // Skip if storage state already exists and is recent (within 1 hour)
  if (fs.existsSync(AUTH_FILE)) {
    const stats = fs.statSync(AUTH_FILE);
    const ageMs = Date.now() - stats.mtimeMs;
    if (ageMs < 3600_000) {
      console.log("Using cached auth state");
      return;
    }
  }

  console.log("Creating fresh auth state...");
  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    // Navigate to auth page
    await page.goto(`${baseURL}/auth`);

    // Wait for the form to be ready
    await page.waitForSelector('input[type="email"]');

    // Fill in login credentials
    await page.fill('input[type="email"]', TEST_USER.email);
    await page.fill('input[type="password"]', TEST_USER.password);

    // Submit the form
    await page.click('button[type="submit"]');

    // Wait for successful redirect to dashboard
    await page.waitForURL("**/dashboard**", { timeout: 30_000 });

    // Verify we're logged in by checking for dashboard content
    await page.waitForSelector('[data-testid="dashboard"]', {
      timeout: 10_000,
    }).catch(() => {
      // Fallback: just wait for any navigation away from auth
      console.log("Dashboard testid not found, using URL check");
    });

    // Save storage state (cookies, localStorage)
    await context.storageState({ path: AUTH_FILE });
    console.log("Auth state saved successfully");
  } catch (error) {
    console.error("Failed to create auth state:", error);

    // Only attempt registration for expected login failures (user doesn't exist)
    // Be specific about which errors trigger registration to avoid hiding real bugs
    const errorMessage = error instanceof Error ? error.message : String(error);
    const isLoginFailure =
      errorMessage.includes("waitForURL") ||
      errorMessage.includes("Timeout") ||
      errorMessage.includes("timeout") ||
      errorMessage.includes("waiting for selector") ||
      (error instanceof Error && error.name === "TimeoutError");

    if (!isLoginFailure) {
      console.error(
        "Unexpected error type - not attempting registration recovery:",
        error instanceof Error ? error.name : typeof error
      );
      throw error;
    }

    console.log("Login failed (user likely doesn't exist), attempting to register test user...");

    try {
      await page.goto(`${baseURL}/auth`);
      await page.waitForSelector('input[type="email"]');

      // Click to switch to register mode
      const toggleButton = page.getByText(/don't have an account|create account/i);
      if (await toggleButton.isVisible()) {
        await toggleButton.click();
        // Wait for mode switch animation
        await page.waitForSelector('input[type="text"]', { timeout: 5_000 });

        // Fill registration form
        await page.fill('input[type="text"]', TEST_USER.name);
        await page.fill('input[type="email"]', TEST_USER.email);
        await page.fill('input[type="password"]', TEST_USER.password);

        // Submit registration
        await page.click('button[type="submit"]');

        // Wait for redirect
        await page.waitForURL("**/dashboard**", { timeout: 30_000 });

        // Save storage state
        await context.storageState({ path: AUTH_FILE });
        console.log("Test user registered and auth state saved");
      } else {
        console.error("Could not find register toggle button");
        throw error;
      }
    } catch (registrationError) {
      console.error("Registration also failed:", registrationError);
      throw registrationError;
    }
  } finally {
    await browser.close();
  }
}

export default globalSetup;
