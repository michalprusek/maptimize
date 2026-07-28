import { defineConfig } from "@playwright/test";

/**
 * Pure-logic tests, run on the same Playwright runner as the E2E suite.
 *
 * Deliberately a second config rather than a project inside playwright.config.ts:
 * that one declares a `webServer`, so folding these in would boot a Next dev
 * server to test functions that never touch a browser or the network.
 *
 *   npm run test:unit
 */
export default defineConfig({
  testDir: "./unit",
  timeout: 10_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  reporter: [["list"]],
});
