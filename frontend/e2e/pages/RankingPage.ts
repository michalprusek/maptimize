import { Page, Locator, expect } from "@playwright/test";

/**
 * Page Object Model for the Ranking page.
 *
 * Handles pairwise comparison and leaderboard interactions.
 */
export class RankingPage {
  readonly page: Page;

  // Comparison view
  readonly comparisonContainer: Locator;
  readonly imageA: Locator;
  readonly imageB: Locator;
  readonly selectAButton: Locator;
  readonly selectBButton: Locator;
  readonly skipButton: Locator;
  readonly undoButton: Locator;

  // Progress indicators
  readonly progressBar: Locator;
  readonly progressText: Locator;
  readonly comparisonCounter: Locator;

  // Leaderboard view
  readonly leaderboardTab: Locator;
  readonly comparisonTab: Locator;
  readonly leaderboardTable: Locator;
  readonly leaderboardRows: Locator;

  // Filters
  readonly experimentFilter: Locator;
  readonly proteinFilter: Locator;

  // Empty states
  readonly emptyState: Locator;
  readonly noMorePairs: Locator;
  readonly noMetricsState: Locator;

  constructor(page: Page) {
    this.page = page;

    // Comparison view
    this.comparisonContainer = page.locator('[data-testid="comparison-container"]').or(
      page.locator('[class*="comparison"]')
    );
    this.imageA = page.locator('[data-testid="image-a"]').or(
      page.locator('[class*="image-a"], [class*="left"]').filter({ has: page.locator("img") })
    );
    this.imageB = page.locator('[data-testid="image-b"]').or(
      page.locator('[class*="image-b"], [class*="right"]').filter({ has: page.locator("img") })
    );
    this.selectAButton = page.locator('[data-testid="select-a"]').or(
      page.locator('button').filter({ hasText: /select.*a|choose.*left/i })
    ).or(this.imageA.locator('button, [role="button"]'));
    this.selectBButton = page.locator('[data-testid="select-b"]').or(
      page.locator('button').filter({ hasText: /select.*b|choose.*right/i })
    ).or(this.imageB.locator('button, [role="button"]'));
    this.skipButton = page.locator('button').filter({ hasText: /skip|tie/i });
    this.undoButton = page.locator('button').filter({ hasText: /undo/i });

    // Progress
    this.progressBar = page.locator('[data-testid="progress-bar"]').or(
      page.locator('[role="progressbar"]')
    );
    this.progressText = page.locator('[data-testid="progress-text"]').or(
      page.locator('text=/\\d+%|convergence/i')
    );
    this.comparisonCounter = page.locator('[data-testid="comparison-counter"]').or(
      page.locator('text=/comparison.*\\d+/i')
    );

    // Leaderboard
    this.leaderboardTab = page.locator('button, [role="tab"]').filter({ hasText: /leaderboard|ranking/i });
    this.comparisonTab = page.locator('button, [role="tab"]').filter({ hasText: /compare|vote/i });
    this.leaderboardTable = page.locator('[data-testid="leaderboard"]').or(
      page.locator('table')
    );
    this.leaderboardRows = this.leaderboardTable.locator('tbody tr');

    // Filters
    this.experimentFilter = page.locator('[data-testid="experiment-filter"]').or(
      page.locator('select').filter({ has: page.locator('option[value*="experiment"]') })
    );
    this.proteinFilter = page.locator('[data-testid="protein-filter"]').or(
      page.locator('select').filter({ has: page.locator('option[value*="protein"]') })
    );

    // Empty states
    this.emptyState = page.locator('[data-testid="empty-state"]').or(
      page.locator('text=/no.*cells|no.*images/i')
    );
    this.noMorePairs = page.locator('[data-testid="no-more-pairs"]').or(
      page.locator('text=/no.*more.*pairs|all.*compared|complete/i')
    );
    this.noMetricsState = page.locator('[data-testid="no-metrics"]').or(
      page.getByRole('heading', { name: /no metrics yet/i })
    ).or(
      page.locator('text=/no metrics yet/i')
    );
  }

  /**
   * Navigate to ranking page.
   */
  async goto(): Promise<void> {
    await this.page.goto("/dashboard/ranking");
    await this.waitForLoad();
  }

  /**
   * Navigate to ranking page for a specific metric.
   */
  async gotoMetric(metricId: number): Promise<void> {
    await this.page.goto(`/dashboard/ranking/${metricId}`);
    await this.waitForLoad();
  }

  /**
   * Wait for ranking page to load.
   */
  async waitForLoad(): Promise<void> {
    await this.page.waitForLoadState("networkidle");
    // Wait for either comparison view, leaderboard, or empty state
    await Promise.race([
      this.comparisonContainer.waitFor({ state: "visible", timeout: 15_000 }),
      this.leaderboardTable.waitFor({ state: "visible", timeout: 15_000 }),
      this.emptyState.waitFor({ state: "visible", timeout: 15_000 }),
      this.noMorePairs.waitFor({ state: "visible", timeout: 15_000 }),
    ]).catch(() => {
      // One of them should be visible
    });
  }

  /**
   * Select image A (left) as winner.
   */
  async selectImageA(): Promise<void> {
    await this.selectAButton.click();
    // Wait for next pair to load
    await this.page.waitForResponse(
      (response) => response.url().includes("/ranking/") && response.ok(),
      { timeout: 10_000 }
    ).catch(() => {});
  }

  /**
   * Select image B (right) as winner.
   */
  async selectImageB(): Promise<void> {
    await this.selectBButton.click();
    // Wait for next pair to load
    await this.page.waitForResponse(
      (response) => response.url().includes("/ranking/") && response.ok(),
      { timeout: 10_000 }
    ).catch(() => {});
  }

  /**
   * Click image to select it as winner.
   */
  async clickImage(side: "a" | "b"): Promise<void> {
    const image = side === "a" ? this.imageA : this.imageB;
    await image.click();
  }

  /**
   * Skip current comparison.
   */
  async skip(): Promise<void> {
    await this.skipButton.click();
  }

  /**
   * Undo last comparison.
   */
  async undo(): Promise<void> {
    await this.undoButton.click();
  }

  /**
   * Switch to leaderboard view.
   */
  async showLeaderboard(): Promise<void> {
    await this.leaderboardTab.click();
    await this.leaderboardTable.waitFor({ state: "visible" });
  }

  /**
   * Switch to comparison view.
   */
  async showComparison(): Promise<void> {
    await this.comparisonTab.click();
    await this.comparisonContainer.waitFor({ state: "visible" });
  }

  /**
   * Get leaderboard row count.
   */
  async getLeaderboardCount(): Promise<number> {
    return await this.leaderboardRows.count();
  }

  /**
   * Get ranking item at position (1-indexed).
   */
  async getRankingItem(position: number): Promise<{ rank: number; score: string }> {
    const row = this.leaderboardRows.nth(position - 1);
    const cells = row.locator("td");

    const rankText = await cells.first().textContent() || "0";
    const scoreText = await cells.nth(2).textContent() || "0";

    return {
      rank: parseInt(rankText, 10),
      score: scoreText,
    };
  }

  /**
   * Use keyboard shortcut for selection.
   */
  async pressKey(key: "1" | "2" | "z" | "ArrowLeft" | "ArrowRight"): Promise<void> {
    await this.page.keyboard.press(key);
    // Wait for API response after keyboard action
    await this.page.waitForResponse(
      (response) => response.url().includes("/ranking/") && response.ok(),
      { timeout: 5000 }
    ).catch(() => {
      // Key press may not trigger API call in all cases
    });
  }

  /**
   * Make multiple comparisons.
   */
  async makeComparisons(count: number): Promise<number> {
    let completed = 0;
    for (let i = 0; i < count; i++) {
      // Check if we have more pairs
      if (await this.noMorePairs.isVisible()) {
        break;
      }
      // Alternate between A and B
      // selectImageA/B already wait for API response
      if (i % 2 === 0) {
        await this.selectImageA();
      } else {
        await this.selectImageB();
      }
      completed++;
    }
    return completed;
  }

  /**
   * Filter by experiment.
   */
  async filterByExperiment(experimentId: string | number): Promise<void> {
    await this.experimentFilter.selectOption(String(experimentId));
    await this.waitForLoad();
  }

  /**
   * Check if comparison view is displayed.
   */
  async isComparisonViewDisplayed(): Promise<boolean> {
    return await this.comparisonContainer.isVisible();
  }

  /**
   * Check if leaderboard is displayed.
   */
  async isLeaderboardDisplayed(): Promise<boolean> {
    return await this.leaderboardTable.isVisible();
  }

  /**
   * Assert comparison view is shown.
   */
  async expectComparisonView(): Promise<void> {
    await expect(this.comparisonContainer).toBeVisible();
    await expect(this.imageA).toBeVisible();
    await expect(this.imageB).toBeVisible();
  }

  /**
   * Assert leaderboard is shown.
   */
  async expectLeaderboard(): Promise<void> {
    await expect(this.leaderboardTable).toBeVisible();
  }

  /**
   * Assert no more pairs available.
   */
  async expectNoMorePairs(): Promise<void> {
    await expect(this.noMorePairs).toBeVisible();
  }
}
