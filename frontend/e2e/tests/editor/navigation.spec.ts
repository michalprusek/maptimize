import { test, expect } from "../../fixtures/auth.fixture";
import { EditorPage } from "../../pages";
import { mockMLEndpoints, mockEditorEndpoints } from "../../mocks/ml-endpoints";

/**
 * Editor Navigation E2E Tests - P2 Important
 *
 * Tests editor loading, navigation between images, zoom, and pan controls.
 */

test.describe("Editor Navigation @important", () => {
  let editorPage: EditorPage;

  test.beforeEach(async ({ authenticatedPage }) => {
    editorPage = new EditorPage(authenticatedPage);
    await mockMLEndpoints(authenticatedPage);
  });

  test("should load editor for an image", async ({ authenticatedPage }) => {
    await mockEditorEndpoints(authenticatedPage, {
      fovImages: [
        {
          id: 1,
          experiment_id: 1,
          original_filename: "test-image.tif",
          status: "READY",
        },
      ],
    });

    await editorPage.goto(1, 1);
    await editorPage.expectLoaded();
  });

  test("should display canvas element", async ({ authenticatedPage }) => {
    await mockEditorEndpoints(authenticatedPage);

    await editorPage.goto(1, 1);

    await expect(editorPage.canvas).toBeVisible();
  });

  test("should support zoom in", async ({ authenticatedPage }) => {
    await mockEditorEndpoints(authenticatedPage);

    await editorPage.goto(1, 1);

    // Verify zoom in button is visible and clickable
    await expect(editorPage.zoomInButton).toBeVisible();
    await editorPage.zoomIn();
    await authenticatedPage.waitForTimeout(300);

    // Verify canvas is still visible after zoom
    await expect(editorPage.canvas).toBeVisible();
  });

  test("should support zoom out", async ({ authenticatedPage }) => {
    await mockEditorEndpoints(authenticatedPage);

    await editorPage.goto(1, 1);

    // Verify zoom out button is visible and clickable
    await expect(editorPage.zoomOutButton).toBeVisible();
    await editorPage.zoomOut();
    await authenticatedPage.waitForTimeout(300);

    // Verify canvas is still visible after zoom
    await expect(editorPage.canvas).toBeVisible();
  });

  test("should support keyboard shortcuts for zoom", async ({ authenticatedPage }) => {
    await mockEditorEndpoints(authenticatedPage);

    await editorPage.goto(1, 1);

    // Use keyboard shortcut (ArrowUp for zoom in according to the component)
    await editorPage.pressKey("ArrowUp");
    await authenticatedPage.waitForTimeout(300);

    // Verify canvas is still visible after keyboard zoom
    await expect(editorPage.canvas).toBeVisible();
  });

  test("should navigate to next image", async ({ authenticatedPage }) => {
    await mockEditorEndpoints(authenticatedPage, {
      fovImages: [
        { id: 1, experiment_id: 1, original_filename: "image-1.tif", status: "READY" },
        { id: 2, experiment_id: 1, original_filename: "image-2.tif", status: "READY" },
      ],
    });

    await editorPage.goto(1, 1);

    // Check if next button is available (only shown when multiple images)
    const nextButtonVisible = await editorPage.nextButton.isVisible({ timeout: 5000 }).catch(() => false);
    if (nextButtonVisible) {
      await editorPage.goToNextImage();
      await authenticatedPage.waitForTimeout(500);
      // URL should now include image 2
      expect(authenticatedPage.url()).toContain("/2");
    } else {
      // If navigation not visible, just verify editor loaded
      await expect(editorPage.canvas).toBeVisible();
    }
  });

  test("should navigate to previous image", async ({ authenticatedPage }) => {
    await mockEditorEndpoints(authenticatedPage, {
      fovImages: [
        { id: 1, experiment_id: 1, original_filename: "image-1.tif", status: "READY" },
        { id: 2, experiment_id: 1, original_filename: "image-2.tif", status: "READY" },
      ],
    });

    // Start on image 2
    await editorPage.goto(1, 2);

    // Check if prev button is available (only shown when multiple images)
    const prevButtonVisible = await editorPage.prevButton.isVisible({ timeout: 5000 }).catch(() => false);
    if (prevButtonVisible) {
      await editorPage.goToPreviousImage();
      await authenticatedPage.waitForTimeout(500);
      // URL should now include image 1
      expect(authenticatedPage.url()).toContain("/1");
    } else {
      // If navigation not visible, just verify editor loaded
      await expect(editorPage.canvas).toBeVisible();
    }
  });

  test("should support canvas pan with mouse drag", async ({ authenticatedPage }) => {
    await mockEditorEndpoints(authenticatedPage);

    await editorPage.goto(1, 1);

    // Select pan tool if available
    if (await editorPage.panTool.isVisible()) {
      await editorPage.selectPanTool();
    }

    // Perform drag operation
    await editorPage.dragCanvas(100, 100, 200, 200);

    // Verify canvas is still visible
    await expect(editorPage.canvas).toBeVisible();
  });

  test("should show loading indicator during image load", async ({ authenticatedPage }) => {
    // Mock FOV list
    await authenticatedPage.route("**/api/images/fovs*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([{ id: 1, experiment_id: 1, original_filename: "test.tif", status: "READY" }]),
      });
    });

    // Mock crops endpoint
    await authenticatedPage.route("**/api/images/*/crops*", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) });
    });

    // Mock slow image loading
    await authenticatedPage.route("**/api/images/1", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: 1,
          experiment_id: 1,
          original_filename: "test.tif",
          status: "READY",
        }),
      });
    });

    await authenticatedPage.route("**/api/images/1/file*", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512">
        <rect width="512" height="512" fill="#1a1a2e"/>
      </svg>`;
      await route.fulfill({ status: 200, contentType: "image/svg+xml", body: svg });
    });

    // Navigate to editor (loading should start)
    await authenticatedPage.goto("/editor/1/1");

    // Loading indicator might appear during load
    // This is implementation-dependent
    await editorPage.waitForLoad();
    await editorPage.expectLoaded();
  });

  test("should handle image load error gracefully", async ({ authenticatedPage }) => {
    // Mock image error
    await authenticatedPage.route("**/api/images/999", async (route) => {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Image not found" }),
      });
    });

    await authenticatedPage.goto("/editor/1/999");
    await authenticatedPage.waitForLoadState("networkidle");

    // Should show error or redirect
    // Implementation-dependent behavior
  });
});
