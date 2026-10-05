const { test, expect } = require("@playwright/test");

test("Gmail shows exact callback with sending disabled", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Gmail · Connect your mailbox", { exact: true }).click();
  await expect(page.locator("#gmail-callback")).toHaveText("http://127.0.0.1:8765/auth/gmail/callback");
  await expect(page.locator("#gmail-connect")).toBeDisabled();
  await expect(page.locator("#gmail-status")).toContainText("missing");
  await expect(page.getByText("Read-only permission;", { exact: false })).toContainText("sending is disabled");
  await expect(page.locator("#gmail-disconnect")).toBeHidden();
  await expect(page.locator("#gmail-refresh")).toBeHidden();
});

test("manual previews are text, paged, and linked only after review", async ({ page }) => {
  const status = {configured: true, connected: true, email: "owner@example.test", redirect_uri: "https://clubsp.online/auth/gmail/callback"};
  let messages = [], syncs = 0;
  await page.route("**/api/gmail/status", route => route.fulfill({json: status}));
  await page.route("**/api/state", async route => {
    const response = await route.fetch();
    const state = await response.json();
    state.properties.push({id: "synthetic-property", address: "123 Synthetic St"});
    state.communications.contacts.push({id: "synthetic-contact", name: "Synthetic Seller", email: "seller@example.test", property_id: "synthetic-property", messages: []});
    await route.fulfill({json: state});
  });
  await page.route("**/api/gmail/inbox", route => route.fulfill({json: {messages, total: messages.length}}));
  await page.route("**/api/gmail/sync", route => {
    const data = route.request().postDataJSON();
    expect(data.limit).toBe(5);
    expect(data.page_token).toBe(syncs ? "synthetic-next" : "");
    syncs++;
    messages = [{id: "synthetic-preview", gmail_id: "abc123", mailbox: status.email, sender: "Synthetic Seller", sender_email: "seller@example.test", subject: "<img src=x onerror=alert(1)>", snippet: "<script>alert(1)</script>", received_at: "2026-10-02"}];
    return route.fulfill({json: {inserted: syncs === 1 ? 1 : 0, duplicates: syncs === 1 ? 0 : 1, next_page_token: syncs === 1 ? "synthetic-next" : ""}});
  });
  await page.route("**/api/gmail/previews/*/review", route => {
    expect(route.request().postDataJSON()).toEqual({action: "link", contact_id: "synthetic-contact"});
    Object.assign(messages[0], {contact_id: "synthetic-contact", contact_name: "Synthetic Seller", property_address: "123 Synthetic St"});
    return route.fulfill({json: {action: "link"}});
  });
  await page.goto("/");
  await page.getByText("Gmail · Connect your mailbox", {exact: true}).click();
  await expect(page.locator("#gmail-inbox-count")).toContainText("0 saved previews");
  expect(syncs).toBe(0);
  await page.locator("#gmail-sync-form button[type=submit]").click();
  await expect(page.locator("#gmail-result")).toContainText("1 new previews");
  await expect(page.locator("#gmail-inbox h3")).toHaveText("<img src=x onerror=alert(1)>");
  await expect(page.locator("#gmail-inbox img, #gmail-inbox script")).toHaveCount(0);
  await expect(page.getByRole("button", {name: "Link preview", exact: true})).toBeDisabled();
  await page.locator("#gmail-next").click();
  await expect(page.locator("#gmail-result")).toContainText("1 duplicates skipped");
  await expect(page.locator("#gmail-sync-form button[type=submit]")).toBeEnabled();
  await page.locator("#gmail-inbox select").selectOption("synthetic-contact");
  await page.getByRole("button", {name: "Link preview", exact: true}).click();
  await expect(page.locator("#gmail-inbox")).toContainText("Linked to Synthetic Seller · 123 Synthetic St");
  expect(await page.locator("#gmail-inbox").evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  expect(await page.locator("#gmail-callback").evaluate(node => node.getBoundingClientRect().right <= innerWidth)).toBe(true);
});

test("authorized mailbox can refresh with approved-send capability", async ({ page }) => {
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
  await expect(page.locator("#gmail-result")).toHaveText("Gmail access refreshed. Approved-draft sending is enabled; automatic sending remains disabled.");
  await expect(page.locator("#gmail-status")).toContainText("Read access plus approved-draft sending.");
});
