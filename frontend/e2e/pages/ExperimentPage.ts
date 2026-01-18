import { Page, Locator, expect } from "@playwright/test";

/**
 * Page Object Model for Experiments pages.
 *
 * Handles experiment list, creation, and detail views.
 */
export class ExperimentPage {
  readonly page: Page;

  // List view locators
  readonly experimentList: Locator;
  readonly createButton: Locator;
  readonly searchInput: Locator;
  readonly experimentCards: Locator;
  readonly emptyState: Locator;

  // Create/Edit modal locators
  readonly modal: Locator;
  readonly nameInput: Locator;
  readonly descriptionInput: Locator;
  readonly proteinSelect: Locator;
  readonly saveButton: Locator;
  readonly cancelButton: Locator;

  // Detail view locators
  readonly experimentTitle: Locator;
  readonly uploadButton: Locator;
  readonly imageGallery: Locator;
  readonly processButton: Locator;
  readonly deleteButton: Locator;

  constructor(page: Page) {
    this.page = page;

    // List view
    this.experimentList = page.locator('[data-testid="experiment-list"]').or(
      page.locator("main")
    );
    this.createButton = page.locator('button').filter({ hasText: /new experiment/i }).first();
    this.searchInput = page.locator('input[placeholder*="search" i]');
    this.experimentCards = page.locator('[data-testid="experiment-card"]').or(
      page.locator('a[href*="/experiments/"]').filter({ has: page.locator('h3, h4, span') })
    );
    this.emptyState = page.locator('[data-testid="empty-state"]').or(
      page.getByRole('heading', { name: /no experiments yet/i })
    );

    // Modal - the modal is a glass-card inside a fixed backdrop
    this.modal = page.locator('.fixed.inset-0 .glass-card').or(
      page.locator('[role="dialog"]')
    );
    this.nameInput = this.modal.locator('input[type="text"]').first();
    this.descriptionInput = this.modal.locator('textarea').first();
    this.proteinSelect = this.modal.locator('button').filter({ has: page.locator('[class*="chevron"]') });
    this.saveButton = this.modal.locator('button[type="submit"]').or(
      this.modal.locator('button').filter({ hasText: /create|save/i })
    );
    this.cancelButton = this.modal.locator('button').filter({ hasText: /cancel/i });

    // Detail view
    this.experimentTitle = page.locator("h1, h2").first();
    this.uploadButton = page.locator('button').filter({ hasText: /upload/i });
    this.imageGallery = page.locator('[data-testid="image-gallery"]').or(
      page.locator('[class*="gallery"]')
    );
    this.processButton = page.locator('button').filter({ hasText: /process|detect/i });
    this.deleteButton = page.locator('button').filter({ hasText: /delete/i });
  }

  /**
   * Navigate to experiments list page.
   */
  async gotoList(): Promise<void> {
    await this.page.goto("/dashboard/experiments");
    await this.waitForList();
  }

  /**
   * Navigate to a specific experiment detail page.
   */
  async gotoDetail(experimentId: number): Promise<void> {
    await this.page.goto(`/dashboard/experiments/${experimentId}`);
    await this.waitForDetail();
  }

  /**
   * Wait for experiment list to load.
   */
  async waitForList(): Promise<void> {
    await this.page.waitForLoadState("networkidle");
    // Wait for either experiment cards or empty state
    await Promise.race([
      this.experimentCards.first().waitFor({ state: "visible", timeout: 10_000 }),
      this.emptyState.waitFor({ state: "visible", timeout: 10_000 }),
      this.createButton.waitFor({ state: "visible", timeout: 10_000 }),
    ]);
  }

  /**
   * Wait for experiment detail to load.
   */
  async waitForDetail(): Promise<void> {
    await this.page.waitForLoadState("networkidle");
    await this.experimentTitle.waitFor({ state: "visible", timeout: 10_000 });
  }

  /**
   * Open create experiment modal.
   */
  async openCreateModal(): Promise<void> {
    await this.createButton.click();
    await this.modal.waitFor({ state: "visible" });
  }

  /**
   * Close the modal.
   */
  async closeModal(): Promise<void> {
    await this.cancelButton.click();
    await this.modal.waitFor({ state: "hidden" });
  }

  /**
   * Create a new experiment.
   */
  async createExperiment(name: string, description?: string): Promise<void> {
    await this.openCreateModal();
    await this.nameInput.fill(name);
    if (description) {
      await this.descriptionInput.fill(description);
    }
    await this.saveButton.click();
    await this.modal.waitFor({ state: "hidden" });
  }

  /**
   * Click on an experiment card by name.
   */
  async clickExperiment(name: string): Promise<void> {
    const card = this.experimentCards.filter({ hasText: name }).first();
    await card.click();
    await this.waitForDetail();
  }

  /**
   * Get the count of experiments in the list.
   */
  async getExperimentCount(): Promise<number> {
    return await this.experimentCards.count();
  }

  /**
   * Check if an experiment with given name exists.
   */
  async experimentExists(name: string): Promise<boolean> {
    const card = this.experimentCards.filter({ hasText: name });
    return await card.count() > 0;
  }

  /**
   * Delete the current experiment (from detail view).
   */
  async deleteExperiment(): Promise<void> {
    await this.deleteButton.click();
    // Wait for confirmation dialog
    const confirmButton = this.page.locator('button').filter({ hasText: /confirm|yes|delete/i });
    await confirmButton.click();
    // Should redirect to list
    await this.page.waitForURL("**/experiments**");
  }

  /**
   * Navigate to upload page for current experiment.
   */
  async goToUpload(): Promise<void> {
    await this.uploadButton.click();
    await this.page.waitForURL("**/upload**");
  }

  /**
   * Get experiment title text.
   */
  async getTitle(): Promise<string> {
    return await this.experimentTitle.textContent() || "";
  }

  /**
   * Get image count in gallery.
   */
  async getImageCount(): Promise<number> {
    const images = this.imageGallery.locator("img, [data-testid='image-item']");
    return await images.count();
  }

  /**
   * Assert experiment list is displayed.
   */
  async expectListDisplayed(): Promise<void> {
    await expect(this.page).toHaveURL(/\/experiments/);
  }

  /**
   * Assert experiment detail is displayed.
   */
  async expectDetailDisplayed(name?: string): Promise<void> {
    await expect(this.page).toHaveURL(/\/experiments\/\d+/);
    if (name) {
      await expect(this.experimentTitle).toContainText(name);
    }
  }

  /**
   * Assert experiment was created successfully.
   */
  async expectExperimentCreated(name: string): Promise<void> {
    // Modal should be closed
    await expect(this.modal).not.toBeVisible();
    // Experiment should appear in list or we should be on detail page
    const inList = await this.experimentExists(name);
    const onDetail = this.page.url().includes("/experiments/");
    expect(inList || onDetail).toBeTruthy();
  }
}
