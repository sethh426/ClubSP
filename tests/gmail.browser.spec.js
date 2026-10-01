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
