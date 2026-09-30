const { test, expect } = require("@playwright/test");

test("property evidence, estimate, and outcome work through the browser", async ({ page, request }, testInfo) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const before = await (await request.get("/api/state")).json();
  await page.goto("/");
  await expect(page.locator("#connection")).toHaveText("Saved locally");
  const address = "123 Browser " + testInfo.project.name + " St";
  const property = page.locator("#property-form");
  await property.locator('[name="address"]').fill(address);
  await property.locator('[name="city"]').fill("Fort Wayne");
  await property.locator('[name="state"]').fill("IN");
  await property.locator('[name="zip"]').fill("46802");
  await property.getByRole("button", { name: "Save property" }).click();
  await expect(page.locator("#property-title")).toHaveText(address);
  await expect(page.locator("#count-properties")).toHaveText(String(before.properties.length + 1));

  const fact = page.locator("#fact-form");
  await fact.locator('[name="attribute"]').fill("sqft");
  await fact.locator('[name="value"]').fill("1800");
  await fact.locator('[name="provider"]').fill("County assessor");
  await fact.locator('[name="url"]').fill("https://example.com/parcel");
  await fact.getByRole("button", { name: "Record fact" }).click();
  await expect(page.locator("#facts")).toContainText("sqft: 1800");
  await expect(page.locator("#facts a")).toHaveAttribute("href", "https://example.com/parcel");

  // User-provided values must appear as text rather than execute HTML.
  await fact.locator('[name="attribute"]').fill("note");
  await fact.locator('[name="value_format"]').selectOption("text");
  await fact.locator('[name="value"]').fill('<img src=x onerror="window.injected=true">');
  await fact.locator('[name="provider"]').fill("Inspection");
  await fact.getByRole("button", { name: "Record fact" }).click();
  await expect(page.locator("#facts")).toContainText('<img src=x onerror="window.injected=true">');
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
  expect(await page.locator("#facts img").count()).toBe(0);

  const prediction = page.locator("#prediction-form");
  await prediction.locator('[name="predicted_value"]').fill("30000");
  await prediction.getByRole("button", { name: "Save estimate" }).click();
  await expect(page.locator("#predictions")).toContainText("Estimated $30,000.00");
  const outcome = page.locator(".outcome-form");
  await outcome.locator('[name="actual_value"]').fill("33000");
  await outcome.locator('[name="provider"]').fill("Contractor invoice");
  await outcome.getByRole("button", { name: "Record outcome" }).click();
  await expect(page.locator("#predictions")).toContainText("Actual $33,000.00");
  await expect(page.locator("#learning")).toContainText("predicted=30000");
  await expect(page.locator(".outcome-form")).toHaveCount(0);

  const dealStart = page.locator("#deal-form");
  await dealStart.locator('[name="strategy"]').selectOption("assignment");
  await dealStart.getByRole("button", { name: "Start deal" }).click();
  const underwriting = page.locator(".underwrite-form");
  await underwriting.locator('[name="basis"]').fill("Browser test assumptions; not a live valuation.");
  await underwriting.getByRole("button", { name: "Save underwriting scenarios" }).click();
  await expect(page.locator("#deal-board")).toContainText("Owner max contract:");
  await expect(page.locator("#deal-board")).toContainText("Manual scenario analysis");
  const buyer = page.locator("#buyer-form");
  await buyer.locator('[name="name"]').fill("Browser Buyer");
  await buyer.locator('[name="locations"]').fill("Fort Wayne, IN");
  await buyer.locator('[name="max_total_price"]').fill("200000");
  await buyer.locator('[name="max_repairs"]').fill("50000");
  await buyer.getByRole("button", { name: "Save buyer" }).click();
  await expect(page.locator("#buyer-results")).toContainText("Browser Buyer");
  await page.getByRole("button", { name: "Run buyer matching" }).click();
  await expect(page.locator("#deal-board")).toContainText("Browser Buyer");
  await expect(page.locator("#deal-board")).toContainText("Funding evidence needs current owner review");

  await page.reload();
  await expect(page.locator("#property-title")).toHaveText(address);
  await expect(page.locator("#facts")).toContainText("sqft: 1800");
  await expect(page.locator("#learning")).toContainText("actual=33000");
  await page.locator("#search").fill("no-matching-address");
  await expect(page.locator("#property-list")).toContainText("No matching properties.");
  await page.locator("#search").fill("");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("workspace.png"), fullPage: true });
});
