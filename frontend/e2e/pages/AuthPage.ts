import { Page, Locator, expect } from "@playwright/test";

/**
 * Page Object Model for the Authentication page.
 *
 * Handles login and registration flows.
 */
export class AuthPage {
  readonly page: Page;

  // Locators
  readonly emailInput: Locator;
  readonly passwordInput: Locator;
  readonly nameInput: Locator;
  readonly submitButton: Locator;
  readonly toggleModeButton: Locator;
  readonly errorMessage: Locator;
  readonly loadingSpinner: Locator;

  constructor(page: Page) {
    this.page = page;

    // Input fields
    this.emailInput = page.locator('input[type="email"]');
    this.passwordInput = page.locator('input[type="password"]');
    this.nameInput = page.locator('input[type="text"]');

    // Buttons
    this.submitButton = page.locator('button[type="submit"]');
    this.toggleModeButton = page.locator("button").filter({
      hasText: /(don't have an account|already have an account|have an account)/i,
    });

    // Messages
    this.errorMessage = page.locator('[class*="accent-red"], [class*="error"], [class*="text-red"]');
    this.loadingSpinner = page.locator('[class*="animate-spin"], svg.animate-spin');
  }

  /**
   * Navigate to the auth page.
   */
  async goto(): Promise<void> {
    await this.page.goto("/auth");
    await this.emailInput.waitFor({ state: "visible" });
  }

  /**
   * Fill the email field.
   */
  async fillEmail(email: string): Promise<void> {
    await this.emailInput.fill(email);
  }

  /**
   * Fill the password field.
   */
  async fillPassword(password: string): Promise<void> {
    await this.passwordInput.fill(password);
  }

  /**
   * Fill the name field (registration mode only).
   */
  async fillName(name: string): Promise<void> {
    await this.nameInput.fill(name);
  }

  /**
   * Submit the form.
   */
  async submit(): Promise<void> {
    await this.submitButton.click();
  }

  /**
   * Switch between login and registration modes.
   */
  async toggleMode(): Promise<void> {
    await this.toggleModeButton.click();
    await this.page.waitForTimeout(300); // Wait for animation
  }

  /**
   * Check if currently in login mode.
   */
  async isLoginMode(): Promise<boolean> {
    const text = await this.submitButton.textContent();
    const lowerText = text?.toLowerCase() || "";
    // Handle "Sign In", "Login", "Log in" etc.
    return lowerText.includes("login") || lowerText.includes("sign in");
  }

  /**
   * Check if currently in registration mode.
   */
  async isRegistrationMode(): Promise<boolean> {
    const text = await this.submitButton.textContent();
    const lowerText = text?.toLowerCase() || "";
    // Handle "Register", "Sign Up", "Create Account" etc.
    return lowerText.includes("register") || lowerText.includes("sign up") || lowerText.includes("create");
  }

  /**
   * Perform login.
   */
  async login(email: string, password: string): Promise<void> {
    // Ensure we're in login mode
    if (await this.isRegistrationMode()) {
      await this.toggleMode();
    }

    await this.fillEmail(email);
    await this.fillPassword(password);
    await this.submit();
  }

  /**
   * Perform registration.
   */
  async register(email: string, name: string, password: string): Promise<void> {
    // Ensure we're in registration mode
    if (await this.isLoginMode()) {
      await this.toggleMode();
    }

    await this.fillName(name);
    await this.fillEmail(email);
    await this.fillPassword(password);
    await this.submit();
  }

  /**
   * Wait for successful login (redirects to dashboard).
   */
  async waitForLoginSuccess(): Promise<void> {
    await this.page.waitForURL("**/dashboard**", { timeout: 30_000 });
  }

  /**
   * Get the error message text.
   */
  async getErrorMessage(): Promise<string | null> {
    if (await this.errorMessage.isVisible()) {
      return await this.errorMessage.textContent();
    }
    return null;
  }

  /**
   * Check if loading spinner is visible.
   */
  async isLoading(): Promise<boolean> {
    return await this.loadingSpinner.isVisible();
  }

  /**
   * Assert successful login.
   */
  async expectLoginSuccess(): Promise<void> {
    await expect(this.page).toHaveURL(/\/dashboard/);
  }

  /**
   * Assert error message is displayed.
   */
  async expectError(message?: string | RegExp): Promise<void> {
    await expect(this.errorMessage).toBeVisible();
    if (message) {
      await expect(this.errorMessage).toContainText(message);
    }
  }
}
