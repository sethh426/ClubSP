const { test, expect } = require("@playwright/test");
const { randomUUID } = require("node:crypto");

test("stage candidate, review sale, preserve withdrawal and render source text safely", async ({ page, request }) => {
  const suffix = randomUUID();
  const address = "Synthetic intake " + suffix;
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator("#connection")).toHaveText("Saved locally");
  await page.locator("#sourcing-panel > summary").click();
  const form = page.locator(".sourcing-import-form");
  await form.locator("..").evaluate(element => { element.open = true; });
  const date = new Date().toISOString().slice(0, 10);
  for (const [name, value] of Object.entries({provider: "Synthetic " + suffix, source_url: "https://example.com/export",
    source_date: date, rights_basis: "Synthetic authorized fixture", city: "Fort Wayne", state: "IN",
    csv: "address,zip,parcel_id,property_type\n" + address + ",46802," + suffix + ",single_family"})) {
    await form.locator('[name="' + name + '"]').fill(value);
  }
  await form.getByRole("button", {name: "Preview import"}).click();
  await expect(page.locator("#message")).toContainText("staged for review");
  let row = page.locator(".sourcing-row").filter({hasText: address});
  await row.locator("..").evaluate(element => { element.open = true; });
  const review = row.locator("form");
  await review.locator('[name="action"]').selectOption("accept");
  for (const name of ["reviewer", "note", "evidence_reference"]) await review.locator('[name="' + name + '"]').fill("Synthetic " + name);
  await review.locator('[name="identity_confirmed"]').check();
  await review.getByRole("button", {name: "Save import review"}).click();
  await expect(page.locator("#message")).toContainText("Import review recorded");
  row = page.locator(".sourcing-row").filter({hasText: address});
  await row.locator("..").evaluate(element => { element.open = true; });
  await row.getByRole("button", {name: "Open reviewed property"}).click();
  await expect(page.locator("#property-title")).toHaveText(address);
  const state = await (await request.get("/api/state")).json();
  const prop = state.properties.find(p => p.address === address);
  expect(prop).toBeTruthy();
  const compAddress = "Synthetic comp " + suffix + " <img src=x onerror=window.injected=true>";
  await request.post("/api/sourcing/import", {data: {kind: "county_sales", provider: "Synthetic sales " + suffix,
    source_url: "https://example.com/sales", source_date: date, rights_basis: "Synthetic fixture", city: "Fort Wayne", state: "IN",
    csv: "Parcel Number,Address,Sale Date,Sale Price,Living Area\ncomp-" + suffix + "," + compAddress + "," + date + ",240000,1800"}});
  await page.reload(); await expect(page.locator("#connection")).toHaveText("Saved locally");
  await page.locator("#sourcing-panel > summary").click();
  const saleRow = page.locator(".sourcing-row").filter({hasText: compAddress});
  await saleRow.locator("..").evaluate(element => { element.open = true; });
  const saleReview = saleRow.locator("form");
  await saleReview.locator('[name="action"]').selectOption("accept");
  await saleReview.locator('[name="property_id"]').selectOption(prop.id);
  for (const name of ["reviewer", "note", "evidence_reference"]) await saleReview.locator('[name="' + name + '"]').fill("Synthetic sale " + name);
  await saleReview.locator('[name="identity_confirmed"]').check(); await saleReview.locator('[name="sale_verified"]').check();
  await saleReview.getByRole("button", {name: "Save import review"}).click();
  await expect(page.locator("#message")).toContainText("Import review recorded");
  const sale = page.locator(".sale-evidence-row").filter({hasText: compAddress});
  await sale.locator("..").evaluate(element => { element.open = true; });
  await expect(sale).toContainText("accepted");
  const screen = page.locator(".comp-screen-row").filter({hasText: compAddress});
  await expect(screen).toContainText("needs review");
  await expect(screen).toContainText("Confirm valid subject and sale living_area");
  await expect(screen).toContainText("No exit-price estimate");
  for (const name of ["reviewer", "note", "evidence_reference"]) await sale.locator('[name="' + name + '"]').fill("Synthetic withdrawal " + name);
  await sale.getByRole("button", {name: "Withdraw comparable"}).click();
  await expect(page.locator("#message")).toContainText("Comparable withdrawn");
  await sale.locator("..").evaluate(element => { element.open = true; });
  await expect(sale).toContainText("withdrawn");
  await expect(page.locator(".comp-screen-row").filter({hasText: compAddress})).toHaveCount(0);
  expect(await page.locator("#sourcing-workspace img").count()).toBe(0);
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
  expect(errors).toEqual([]);
});
