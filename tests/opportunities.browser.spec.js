const { test, expect } = require("@playwright/test");
const { randomUUID } = require("node:crypto");

test("opportunity policy persists and the queue safely renders entered deals", async ({ page, request }, testInfo) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const property = await request.post("/api/properties", { data: {
    address: "Synthetic Queue " + testInfo.project.name + " " + randomUUID() + ' <img src=x onerror="window.injected=true">',
    city: "Fort Wayne", state: "IN",
  }});
  expect(property.ok()).toBeTruthy();
  const prop = await property.json();
  const created = await request.post("/api/deals", { data: { property_id: prop.id, strategy: "assignment" }});
  expect(created.ok()).toBeTruthy();
  await page.goto("/");
  await expect(page.locator("#connection")).toHaveText("Saved locally");
  const form = page.locator(".opportunity-policy-form");
  await form.locator("..").evaluate(element => { element.open = true; });
  await form.locator('[name="markets"]').fill("Fort Wayne, IN");
  await form.locator('[name="property_types"]').fill("single_family");
  for (const [key, value] of Object.entries({ max_seller_price: "150000", max_deal_cash_at_risk: "10000",
    max_portfolio_cash_at_risk: "20000", min_downside_net: "0", evidence_max_age_days: "30" })) {
    await form.locator('[name="' + key + '"]').fill(value);
  }
  await form.locator('[name="basis"]').fill("Synthetic browser policy, not an operating mandate");
  await form.getByRole("button", { name: "Save opportunity policy" }).click();
  await expect(page.locator("#message")).toContainText("No external actions authorized");
  const card = page.locator(".opportunity-card").filter({ hasText: prop.address });
  await expect(card).toContainText("research");
  await expect(card).toContainText("owner-of-record evidence");
  expect(await page.locator("#opportunity-queue img").count()).toBe(0);
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
  await card.getByRole("button", { name: "Open deal file" }).click();
  await expect(page.locator("#property-title")).toHaveText(prop.address);
  await page.reload();
  await expect(page.locator("#connection")).toHaveText("Saved locally");
  await expect(page.locator('.opportunity-policy-form [name="max_seller_price"]')).toHaveValue("150000");
  await expect(page.locator(".opportunity-card").filter({ hasText: prop.address })).toContainText("research");
  expect(errors).toEqual([]);
});
