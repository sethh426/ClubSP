const { test, expect } = require("@playwright/test");

test("Commitment Graph records standing demand and stays usable in browser", async ({ page, request }, testInfo) => {
  const suffix = testInfo.project.name + "-" + Date.now();
  const buyerName = "Synthetic Commitment Buyer " + suffix;
  const buyerResponse = await request.post("/api/buyers", { data: {
    name: buyerName,
    company: "Synthetic Fixture LLC",
    locations: ["Fort Wayne, IN"],
    strategies: ["assignment"],
    property_types: ["single_family"],
    max_total_price: 160000,
    max_repairs: 50000,
    funding_status: "unverified",
    verified_at: "",
    verification_reference: "",
  }});
  expect(buyerResponse.ok()).toBeTruthy();

  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator("#connection")).toHaveText("Saved locally");
  await expect(page.getByRole("heading", { name: "Commitment Graph" })).toBeVisible();

  const form = page.locator(".commitment-mandate-form");
  await form.locator("..").evaluate(element => { element.open = true; });
  await form.locator('[name="buyer_id"]').selectOption({ label: buyerName + " · Synthetic Fixture LLC" });
  await form.locator('[name="markets"]').fill("Fort Wayne, IN");
  await form.locator('[name="strategies"]').selectOption("assignment");
  await form.locator('[name="property_types"]').fill("single_family");
  await form.locator('[name="max_total_price"]').fill("160000");
  await form.locator('[name="max_repairs"]').fill("50000");
  await form.locator('[name="priority"]').fill("90");
  await form.locator('[name="verified_at"]').fill("2026-10-04");
  await form.locator('[name="evidence_reference"]').fill("synthetic-browser-confirmation");
  await form.getByRole("button", { name: "Save standing mandate" }).click();

  await expect(page.locator("#commitment-workspace")).toContainText(buyerName);
  await expect(page.locator("#commitment-workspace")).toContainText("Demand-first search intents");
  await expect(page.locator("#commitment-workspace")).toContainText("Fort Wayne, IN");
  expect(errors).toEqual([]);
});
