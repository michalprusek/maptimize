import { Page, Locator, expect } from "@playwright/test";

/**
 * Page Object Model for the Dashboard page.
 *
 * Handles navigation and dashboard overview interactions.
 */
export class DashboardPage {
  readonly page: Page;

  // Navigation locators
  readonly sidebar: Locator;
  readonly experimentsLink: Locator;
  readonly rankingLink: Locator;
  readonly settingsLink: Locator;
  readonly metricsLink: Locator;
  readonly proteinsLink: Locator;

  // User menu
  readonly userMenu: Locator;
  readonly logoutButton: Locator;

  // Dashboard content
  readonly welcomeMessage: Locator;
  readonly recentExperiments: Locator;

  constructor(page: Page) {
    this.page = page;

    // Sidebar navigation
    this.sidebar = page.locator('nav, [role="navigation"]');
    this.experimentsLink = page.locator('a[href*="/experiments"]').first();
    this.rankingLink = page.locator('a[href*="/ranking"]').first();
    this.settingsLink = page.locator('a[href*="/settings"]').first();
    this.metricsLink = page.locator('a[href*="/metrics"]').first();
    this.proteinsLink = page.locator('a[href*="/proteins"]').first();

    // User interactions
    this.userMenu = page.locator('[data-testid="user-menu"]').or(
      page.locator('button').filter({ has: page.locator('img[alt*="avatar" i]') })
    );
    this.logoutButton = page.locator('button').filter({ hasText: /logout|sign out/i });

    // Dashboard content
    this.welcomeMessage = page.locator('h1, h2').filter({ hasText: /welcome|dashboard/i });
    this.recentExperiments = page.locator('[data-testid="recent-experiments"]');
  }

  /**
   * Navigate to dashboard.
   */
  async goto(): Promise<void> {
    await this.page.goto("/dashboard");
    await this.waitForLoad();
  }

  /**
   * Wait for dashboard to load.
   */
  async waitForLoad(): Promise<void> {
    await this.page.waitForLoadState("networkidle");
    // Wait for main content to be visible
    await this.page.locator("main").waitFor({ state: "visible", timeout: 15_000 });
  }

  /**
   * Navigate to Experiments page.
   */
  async goToExperiments(): Promise<void> {
    await this.experimentsLink.click();
    await this.page.waitForURL("**/experiments**");
  }

  /**
   * Navigate to Ranking page.
   */
  async goToRanking(): Promise<void> {
    await this.rankingLink.click();
    await this.page.waitForURL("**/ranking**");
  }

  /**
   * Navigate to Settings page.
   */
  async goToSettings(): Promise<void> {
    await this.settingsLink.click();
    await this.page.waitForURL("**/settings**");
  }

  /**
   * Navigate to Metrics page.
   */
  async goToMetrics(): Promise<void> {
    await this.metricsLink.click();
    await this.page.waitForURL("**/metrics**");
  }

  /**
   * Navigate to Proteins page.
   */
  async goToProteins(): Promise<void> {
    await this.proteinsLink.click();
    await this.page.waitForURL("**/proteins**");
  }

  /**
   * Logout from the application.
   */
  async logout(): Promise<void> {
    // Clear token from localStorage
    await this.page.evaluate(() => {
      localStorage.removeItem("token");
    });
    await this.page.goto("/auth");
  }

  /**
   * Check if user is on dashboard.
   */
  async isOnDashboard(): Promise<boolean> {
    return this.page.url().includes("/dashboard");
  }

  /**
   * Get the current page title.
   */
  async getPageTitle(): Promise<string> {
    const heading = this.page.locator("h1").first();
    return await heading.textContent() || "";
  }

  /**
   * Assert dashboard is loaded.
   */
  async expectLoaded(): Promise<void> {
    await expect(this.page).toHaveURL(/\/dashboard/);
    await expect(this.page.locator("main")).toBeVisible();
  }

  /**
   * Assert navigation item is active.
   */
  async expectNavItemActive(navItem: "experiments" | "ranking" | "settings"): Promise<void> {
    const linkMap = {
      experiments: this.experimentsLink,
      ranking: this.rankingLink,
      settings: this.settingsLink,
    };

    const link = linkMap[navItem];
    // Check for active state class or aria-current
    // Use soft assertions and check either condition
    const hasAriaCurrent = await link.getAttribute("aria-current") === "page";
    const classAttr = await link.getAttribute("class") || "";
    const hasActiveClass = /active|selected/.test(classAttr);
    expect(hasAriaCurrent || hasActiveClass).toBeTruthy();
  }
}
