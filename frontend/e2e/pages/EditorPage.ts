import { Page, Locator, expect } from "@playwright/test";

/**
 * Page Object Model for the Image Editor page.
 *
 * Handles cell crop editing, navigation, zoom, and segmentation interactions.
 */
export class EditorPage {
  readonly page: Page;

  // Canvas and main view
  readonly canvas: Locator;
  readonly imageContainer: Locator;
  readonly loadingIndicator: Locator;

  // Navigation controls
  readonly prevButton: Locator;
  readonly nextButton: Locator;
  readonly imageCounter: Locator;

  // Zoom controls
  readonly zoomInButton: Locator;
  readonly zoomOutButton: Locator;
  readonly zoomResetButton: Locator;
  readonly zoomLevel: Locator;

  // Toolbar
  readonly toolbar: Locator;
  readonly panTool: Locator;
  readonly selectTool: Locator;
  readonly drawTool: Locator;
  readonly segmentTool: Locator;

  // Cell crop panel
  readonly cropPanel: Locator;
  readonly cropList: Locator;
  readonly addCropButton: Locator;
  readonly deleteCropButton: Locator;
  readonly saveCropsButton: Locator;

  // Segmentation controls
  readonly segmentButton: Locator;
  readonly clearSegmentButton: Locator;
  readonly saveSegmentButton: Locator;

  // Display mode controls
  readonly displayModeSelect: Locator;

  constructor(page: Page) {
    this.page = page;

    // Main view
    this.canvas = page.locator("canvas").first();
    this.imageContainer = page.locator('[data-testid="image-container"]').or(
      page.locator('[class*="editor"]').filter({ has: page.locator("canvas") })
    );
    this.loadingIndicator = page.locator('[data-testid="loading"]').or(
      page.locator('[class*="loading"]')
    );

    // Navigation - use title attributes
    this.prevButton = page.locator('button[title="Previous image"]');
    this.nextButton = page.locator('button[title="Next image"]');
    this.imageCounter = page.locator('[data-testid="image-counter"]').or(
      page.locator('text=/\\d+\\s*\\/\\s*\\d+/').first()
    );

    // Zoom - use specific title attributes
    this.zoomInButton = page.locator('button[title="Zoom in"]');
    this.zoomOutButton = page.locator('button[title="Zoom out"]');
    this.zoomResetButton = page.locator('button[title="Fit to view"]').or(
      page.locator('button').filter({ hasText: /reset|fit/i })
    );
    // Zoom level is displayed between zoom in/out buttons, not the brightness/contrast values
    this.zoomLevel = page.locator('[data-testid="zoom-level"]').or(
      page.locator('button[title="Zoom out"]').locator('..').locator('span').filter({ hasText: /\\d+%/ })
    );

    // Toolbar
    this.toolbar = page.locator('[data-testid="toolbar"]').or(
      page.locator('[class*="toolbar"]')
    );
    this.panTool = page.locator('[data-testid="tool-pan"]').or(
      page.locator('button[title*="pan" i]')
    );
    this.selectTool = page.locator('[data-testid="tool-select"]').or(
      page.locator('button[title*="select" i]')
    );
    this.drawTool = page.locator('[data-testid="tool-draw"]').or(
      page.locator('button[title*="draw" i]')
    );
    this.segmentTool = page.locator('[data-testid="tool-segment"]').or(
      page.locator('button[title*="segment" i]')
    );

    // Crop panel
    this.cropPanel = page.locator('[data-testid="crop-panel"]').or(
      page.locator('[class*="crop-panel"]')
    );
    this.cropList = page.locator('[data-testid="crop-list"]').or(
      page.locator('[class*="crop-list"]')
    );
    this.addCropButton = page.locator('button').filter({ hasText: /add.*crop/i });
    this.deleteCropButton = page.locator('button').filter({ hasText: /delete.*crop/i });
    this.saveCropsButton = page.locator('button').filter({ hasText: /save/i });

    // Segmentation
    this.segmentButton = page.locator('button').filter({ hasText: /segment/i });
    this.clearSegmentButton = page.locator('button').filter({ hasText: /clear/i });
    this.saveSegmentButton = page.locator('button').filter({ hasText: /save.*mask/i });

    // Display mode
    this.displayModeSelect = page.locator('[data-testid="display-mode"]').or(
      page.locator('select').filter({ has: page.locator('option[value*="gray"]') })
    );
  }

  /**
   * Navigate to editor for a specific experiment and image.
   */
  async goto(experimentId: number, imageId: number): Promise<void> {
    await this.page.goto(`/editor/${experimentId}/${imageId}`);
    await this.waitForLoad();
  }

  /**
   * Wait for editor to fully load.
   */
  async waitForLoad(): Promise<void> {
    await this.page.waitForLoadState("networkidle");
    // Wait for canvas to be visible and loading to finish
    await this.canvas.waitFor({ state: "visible", timeout: 30_000 });
    await this.loadingIndicator.waitFor({ state: "hidden", timeout: 30_000 }).catch(() => {
      // Loading indicator may not exist
    });
  }

  /**
   * Navigate to next image.
   */
  async goToNextImage(): Promise<void> {
    await this.nextButton.click();
    await this.waitForLoad();
  }

  /**
   * Navigate to previous image.
   */
  async goToPreviousImage(): Promise<void> {
    await this.prevButton.click();
    await this.waitForLoad();
  }

  /**
   * Get current image counter (e.g., "3 / 10").
   */
  async getImageCounter(): Promise<string> {
    return await this.imageCounter.textContent() || "";
  }

  /**
   * Zoom in.
   */
  async zoomIn(): Promise<void> {
    await this.zoomInButton.click();
  }

  /**
   * Zoom out.
   */
  async zoomOut(): Promise<void> {
    await this.zoomOutButton.click();
  }

  /**
   * Reset zoom to fit view.
   */
  async resetZoom(): Promise<void> {
    await this.zoomResetButton.click();
  }

  /**
   * Get current zoom level percentage.
   */
  async getZoomLevel(): Promise<number> {
    const text = await this.zoomLevel.textContent() || "100";
    const match = text.match(/(\d+)/);
    return match ? parseInt(match[1], 10) : 100;
  }

  /**
   * Select pan tool.
   */
  async selectPanTool(): Promise<void> {
    await this.panTool.click();
  }

  /**
   * Select selection tool.
   */
  async selectSelectTool(): Promise<void> {
    await this.selectTool.click();
  }

  /**
   * Click on canvas at specific position.
   */
  async clickCanvas(x: number, y: number): Promise<void> {
    const box = await this.canvas.boundingBox();
    if (box) {
      await this.canvas.click({
        position: { x: Math.min(x, box.width - 1), y: Math.min(y, box.height - 1) },
      });
    }
  }

  /**
   * Drag on canvas from one point to another.
   */
  async dragCanvas(fromX: number, fromY: number, toX: number, toY: number): Promise<void> {
    const box = await this.canvas.boundingBox();
    if (box) {
      await this.page.mouse.move(box.x + fromX, box.y + fromY);
      await this.page.mouse.down();
      await this.page.mouse.move(box.x + toX, box.y + toY);
      await this.page.mouse.up();
    }
  }

  /**
   * Get crop count in the crop panel.
   */
  async getCropCount(): Promise<number> {
    const items = this.cropList.locator('[data-testid="crop-item"], [class*="crop-item"], li');
    return await items.count();
  }

  /**
   * Save all crop changes.
   */
  async saveCrops(): Promise<void> {
    await this.saveCropsButton.click();
    // Wait for save to complete
    await this.page.waitForResponse((response) =>
      response.url().includes("/crops") && response.ok()
    );
  }

  /**
   * Change display mode.
   */
  async setDisplayMode(mode: "grayscale" | "inverted" | "green" | "fire"): Promise<void> {
    await this.displayModeSelect.selectOption(mode);
  }

  /**
   * Assert editor is loaded.
   */
  async expectLoaded(): Promise<void> {
    await expect(this.canvas).toBeVisible();
  }

  /**
   * Assert zoom level.
   */
  async expectZoomLevel(level: number, tolerance = 5): Promise<void> {
    const currentLevel = await this.getZoomLevel();
    expect(Math.abs(currentLevel - level)).toBeLessThanOrEqual(tolerance);
  }

  /**
   * Use keyboard shortcut.
   */
  async pressKey(key: string): Promise<void> {
    await this.page.keyboard.press(key);
  }
}
