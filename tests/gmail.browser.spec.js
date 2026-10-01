const { test, expect } = require("@playwright/test");

test("Gmail shows exact local callback and does not imply sending or sync", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Gmail · Connect your mailbox", { exact: true }).click();
  await expect(page.locator("#gmail-callback")).toHaveText("http://127.0.0.1:8765/auth/gmail/callback");
  await expect(page.locator("#gmail-connect")).toBeDisabled();
  await expect(page.locator("#gmail-status")).toContainText("missing");
  await expect(page.getByText("Read-only permission;", { exact: false })).toContainText("no messages sent or imported");
  await expect(page.locator("#gmail-disconnect")).toBeHidden();
  await expect(page.locator("#gmail-refresh")).toBeHidden();
});

test("authorized mailbox can refresh without claiming inbox import or sending", async ({ page }) => {
  const status = {configured: true, connected: true, email: "owner@example.test",
    access_token_expired: true, redirect_uri: "https://clubsp.online/auth/gmail/callback"};
  await page.route("**/api/gmail/status", route => route.fulfill({json: status}));
  await page.route("**/api/gmail/refresh", route => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().postData()).toBe("{}");
    status.access_token_expired = false;
    return route.fulfill({json: status});
  });
  await page.goto("/");
  await page.getByText("Gmail · Connect your mailbox", {exact: true}).click();
  await expect(page.locator("#gmail-status")).toContainText("expired");
  await page.locator("#gmail-refresh").click();
  await expect(page.locator("#gmail-result")).toHaveText("Gmail access refreshed. Sending and inbox sync remain disabled.");
  await expect(page.locator("#gmail-status")).toContainText("Read-only access.");
});
