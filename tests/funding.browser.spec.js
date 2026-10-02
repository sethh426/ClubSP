const { test, expect } = require("@playwright/test");

test("funding evidence persists and changed deal terms require review", async ({page, request}, testInfo) => {
  const suffix = `${Date.now()}-${Math.random()}`;
  const post = async (path, data) => {
    const response = await request.post(path, {data});
    expect(response.ok()).toBeTruthy(); return response.json();
  };
  const property = await post("/api/properties", {address: `Synthetic funding ${suffix}`, city: "Fort Wayne", state: "IN"});
  const deal = await post("/api/deals", {property_id: property.id, strategy: "resale"});
  await post(`/api/deals/${deal.id}/underwriting`, {
    property_type: "single_family", expected_exit_price: 240000, buyer_repairs: 30000,
    buyer_funding_holding: 8000, buyer_closing: 5000, buyer_selling_costs: 10000,
    buyer_minimum_profit: 42000, target_assignment_fee: 20000, owner_transaction_costs: 4000,
    partner_payout_allowance: 2000, contingency: 2000, desired_owner_net: 10000, basis: "Synthetic only"
  });
  const plan = {seller_price: 125000, assignment_fee: 0, planned_cash_at_risk: 6000, max_cash_at_risk: 10000, basis: "Synthetic only"};
  await post(`/api/deals/${deal.id}/financial-plan`, plan);
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  await page.goto("/funding");
  const card = page.locator(`[data-deal-id="${deal.id}"]`);
  await expect(card).toContainText("External shortfall: Unknown");
  await card.getByText("Record partner or lender funding", {exact: true}).click();
  await card.getByLabel("Partner or lender name", {exact: true}).fill("<img src=x onerror=alert(1)>");
  await card.getByRole("combobox", {name: "Recorded status", exact: true}).selectOption("owner_reviewed");
  for (const [label, value] of [["External funding needed ($)","130000"],["Amount earmarked for this deal ($)","130000"],["Your cash required ($)","6000"],["Additional owner liability / guarantee ($)","0"],["Known financing / partner costs ($)","2000"]]) {
    await card.getByLabel(label, {exact: true}).fill(value);
  }
  const state = await (await request.get("/api/state")).json();
  await card.getByLabel("Evidence review date", {exact: true}).fill(state.today);
  await card.getByLabel("Commitment / terms expire on", {exact: true}).fill("2099-12-31");
  for (const label of ["Funding requirement basis reference","Commitment / terms evidence reference","Deal-specific funding allocation reference","Recourse / guarantees / repayment review reference","Costs reconciled with underwriting reference","All conditions resolved / none: evidence reference"]) {
    await card.getByLabel(label, {exact: true}).fill(`synthetic-${label}-${suffix}`);
  }
  await card.getByLabel("Review notes and limits", {exact: true}).fill("Synthetic evidence only");
  await card.getByRole("checkbox").check();
  await card.getByRole("button", {name: "Save funding review"}).click();
  await expect(card).toContainText("Recorded funding checks pass");
  await expect(card).toContainText("Deal action plan");
  await expect(card).toContainText("Reconciled net: Unknown");
  await page.locator("#funding-filter").selectOption("owner_review");
  await expect(card).toBeHidden();
  await page.locator("#funding-filter").selectOption("needs_action");
  await expect(card).toBeVisible();
  await page.locator("#funding-shortlist").getByRole("link").first().click();
  await expect(page.locator("#funding-filter")).toHaveValue("active");
  await expect(card.locator("img")).toHaveCount(0);
  await page.reload();
  await expect(card).toContainText("Funding history · 1 version");
  await post(`/api/deals/${deal.id}/financial-plan`, {...plan, seller_price: 124000});
  await page.reload();
  await expect(card).toContainText("Deal terms or underwriting changed; review funding again");
  await card.screenshot({path: testInfo.outputPath("funding-card.png")});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});
