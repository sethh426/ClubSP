const { test, expect } = require("@playwright/test");

test("Gmail shows exact local callback and does not imply sending or sync", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Gmail · Connect your mailbox", { exact: true }).click();
  await expect(page.locator("#gmail-callback")).toHaveText("http://127.0.0.1:8765/auth/gmail/callback");
  await expect(page.locator("#gmail-connect")).toBeDisabled();
  await expect(page.locator("#gmail-status")).toContainText("missing");
  await expect(page.getByText("Local authorization only.", { exact: false })).toContainText("no messages sent or imported");
  await expect(page.locator("#gmail-disconnect")).toBeHidden();
});

test("private HTTPS authorization explains and blocks a different browser origin", async ({ page }) => {
  await page.route("**/api/gmail/status", route => route.fulfill({
    json: { configured: true, connected: false, expected_email: "owner@example.test",
      authorization_mode: "private_https",
      redirect_uri: "https://clubsp.online/auth/gmail/callback" },
  }));
  await page.goto("/");
  await page.getByText("Gmail · Connect your mailbox", { exact: true }).click();
  await expect(page.locator("#gmail-callback")).toHaveText("https://clubsp.online/auth/gmail/callback");
  await expect(page.locator("#gmail-setup")).toContainText("configured HTTPS address");
  await expect(page.locator("#gmail-connect")).toBeDisabled();
});

