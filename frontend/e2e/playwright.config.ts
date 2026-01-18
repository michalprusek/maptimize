import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E test configuration for Maptimize.
 *
 * Run tests:
 *   npm run test:e2e           - Run all tests
 *   npm run test:e2e:critical  - Run only @critical tests
 *   npm run test:e2e:ui        - Run with UI mode
 *   npm run test:e2e:debug     - Run with debugging
 *
 * Environment variables:
 *   BASE_URL - Override the base URL (default: http://localhost:3000)
 *   CI       - Set to 'true' in CI environments for optimized settings
 */

const isCI = !!process.env.CI;
const baseURL = process.env.BASE_URL || "http://localhost:3000";

export default defineConfig({
  // Test directory (relative to this config file)
  testDir: "./tests",

  // Maximum time for a single test (2 minutes)
  timeout: 120_000,

  // Maximum time to wait for expect() assertions
  expect: {
    timeout: 10_000,
  },

  // Run tests in parallel
  fullyParallel: true,

  // Fail the build on CI if test.only is left in code
  forbidOnly: isCI,

  // Retries: 2 in CI (flaky network), 0 locally (fast feedback)
  retries: isCI ? 2 : 0,

  // Limit parallel workers in CI to avoid resource issues
  workers: isCI ? 2 : undefined,

  // Reporter configuration
  reporter: isCI
    ? [["github"], ["html", { outputFolder: "./playwright-report", open: "never" }]]
    : [["list"], ["html", { outputFolder: "./playwright-report", open: "on-failure" }]],

  // Output directory for test artifacts (relative to this config file)
  outputDir: "./test-results",

  // Global setup for authentication
  globalSetup: require.resolve("./fixtures/global-setup"),

  // Shared settings for all projects
  use: {
    // Base URL for all navigation
    baseURL,

    // Collect trace on first retry for debugging failures
    trace: "on-first-retry",

    // Record video on first retry
    video: "on-first-retry",

    // Take screenshot on failure
    screenshot: "only-on-failure",

    // Use storage state from global setup (authenticated session)
    storageState: "./fixtures/.auth/user.json",

    // Viewport size
    viewport: { width: 1280, height: 720 },

    // Action timeout
    actionTimeout: 15_000,

    // Navigation timeout
    navigationTimeout: 30_000,
  },

  // Browser projects
  projects: [
    // Setup project - runs first to create authenticated state
    {
      name: "setup",
      testMatch: /global-setup\.ts/,
      use: {
        storageState: undefined, // No storage state for setup
      },
    },

    // Main test project - uses Chromium
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
      },
      dependencies: ["setup"],
    },

    // Mobile viewport tests (optional)
    {
      name: "mobile",
      use: {
        ...devices["iPhone 13"],
      },
      dependencies: ["setup"],
      // Only run on specific tags
      testMatch: /@mobile/,
    },
  ],

  // Web server configuration - auto-start dev server if not running
  webServer: {
    command: "npm run dev",
    url: baseURL,
    reuseExistingServer: !isCI,
    timeout: 120_000,
  },
});
