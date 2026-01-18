import { Page } from "@playwright/test";
import path from "path";
import fs from "fs";

/**
 * Test data helpers for E2E tests.
 *
 * Provides utilities for:
 * - Creating test experiments
 * - Generating unique identifiers
 * - Managing test files
 */

// Backend API URL - in dev mode, API runs on port 8000
const API_BASE_URL = process.env.API_BASE_URL || "http://localhost:8000";

/**
 * Generate a unique test ID for resource naming.
 */
export function generateTestId(): string {
  const timestamp = Date.now().toString(36);
  const random = Math.random().toString(36).substring(2, 8);
  return `e2e_${timestamp}_${random}`;
}

/**
 * Generate a unique experiment name.
 */
export function generateExperimentName(): string {
  return `Test Experiment ${generateTestId()}`;
}

/**
 * Create a test experiment via API.
 * Uses page.evaluate to access localStorage token.
 */
export async function createTestExperiment(
  page: Page,
  name?: string
): Promise<{ id: number; name: string }> {
  const experimentName = name || generateExperimentName();

  // Use page.evaluate to make authenticated API call from browser context
  // Pass the full backend URL since relative paths go to Next.js, not the backend
  const result = await page.evaluate(async ({ expName, apiUrl }) => {
    const token = localStorage.getItem("token");
    const response = await fetch(`${apiUrl}/api/experiments`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`,
      },
      body: JSON.stringify({
        name: expName,
        description: "E2E test experiment",
      }),
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Failed to create experiment: ${text}`);
    }

    return await response.json();
  }, { expName: experimentName, apiUrl: API_BASE_URL });

  return { id: result.id, name: result.name };
}

/**
 * Delete a test experiment via API.
 * Uses page.evaluate to access localStorage token.
 */
export async function deleteTestExperiment(
  page: Page,
  experimentId: number
): Promise<void> {
  await page.evaluate(async ({ id, apiUrl }) => {
    const token = localStorage.getItem("token");
    const response = await fetch(`${apiUrl}/api/experiments/${id}`, {
      method: "DELETE",
      headers: {
        "Authorization": `Bearer ${token}`,
      },
    });

    if (!response.ok && response.status !== 404) {
      console.warn(`Failed to delete experiment ${id}`);
    }
  }, { id: experimentId, apiUrl: API_BASE_URL });
}

/**
 * Clean up all E2E test experiments.
 * Deletes experiments with names matching "e2e_" prefix.
 */
export async function cleanupTestExperiments(page: Page): Promise<number> {
  const deletedCount = await page.evaluate(async (apiUrl) => {
    const token = localStorage.getItem("token");
    const response = await fetch(`${apiUrl}/api/experiments`, {
      headers: { "Authorization": `Bearer ${token}` },
    });

    if (!response.ok) {
      console.warn("Failed to fetch experiments for cleanup");
      return 0;
    }

    const experiments = await response.json();
    let count = 0;

    for (const exp of experiments) {
      if (exp.name.includes("e2e_") || exp.name.includes("Test Experiment")) {
        await fetch(`${apiUrl}/api/experiments/${exp.id}`, {
          method: "DELETE",
          headers: { "Authorization": `Bearer ${token}` },
        });
        count++;
      }
    }

    return count;
  }, API_BASE_URL);

  return deletedCount;
}

/**
 * Test image paths.
 * Uses small test images for faster tests.
 */
export const TEST_IMAGES = {
  // Path to a small test TIFF image (should be in e2e/fixtures/images/)
  smallTiff: path.join(__dirname, "images/test-small.tif"),
  // Path to a larger test image for upload tests
  microscopyImage: path.join(__dirname, "images/test-microscopy.tif"),
};

/**
 * Check if test images exist.
 */
export function testImagesAvailable(): boolean {
  return fs.existsSync(TEST_IMAGES.smallTiff);
}

/**
 * Create a minimal test TIFF image if it doesn't exist.
 * This is a placeholder - real tests should use actual microscopy images.
 */
export async function ensureTestImages(): Promise<void> {
  const imagesDir = path.join(__dirname, "images");

  if (!fs.existsSync(imagesDir)) {
    fs.mkdirSync(imagesDir, { recursive: true });
  }

  // Note: For real tests, copy actual test images to e2e/fixtures/images/
  // This placeholder just creates the directory structure
}

/**
 * Metric test data.
 */
export function generateMetricName(): string {
  return `Test Metric ${generateTestId()}`;
}

/**
 * Create a test metric via API.
 * Uses page.evaluate to access localStorage token.
 */
export async function createTestMetric(
  page: Page,
  name?: string
): Promise<{ id: number; name: string }> {
  const metricName = name || generateMetricName();

  const result = await page.evaluate(async ({ mName, apiUrl }) => {
    const token = localStorage.getItem("token");
    const response = await fetch(`${apiUrl}/api/metrics`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`,
      },
      body: JSON.stringify({
        name: mName,
        description: "E2E test metric",
      }),
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Failed to create metric: ${text}`);
    }

    return await response.json();
  }, { mName: metricName, apiUrl: API_BASE_URL });

  return { id: result.id, name: result.name };
}

/**
 * Delete a test metric via API.
 * Uses page.evaluate to access localStorage token.
 */
export async function deleteTestMetric(
  page: Page,
  metricId: number
): Promise<void> {
  await page.evaluate(async ({ id, apiUrl }) => {
    const token = localStorage.getItem("token");
    const response = await fetch(`${apiUrl}/api/metrics/${id}`, {
      method: "DELETE",
      headers: {
        "Authorization": `Bearer ${token}`,
      },
    });

    if (!response.ok && response.status !== 404) {
      console.warn(`Failed to delete metric ${id}`);
    }
  }, { id: metricId, apiUrl: API_BASE_URL });
}
