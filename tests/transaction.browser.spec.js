const { test, expect } = require("@playwright/test");

async function post(request, path, data) {
  const response = await request.post(path, {data});
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

test("transaction file preserves document versions and separates signature from funds", async ({ page, request }, testInfo) => {
  const address = "901 Transaction " + testInfo.project.name + " Ave";
  const property = await post(request, "/api/properties", {
    address, city: "Fort Wayne", state: "IN", zip: "46802"
  });
  const deal = await post(request, "/api/deals", {property_id: property.id, strategy: "assignment"});
  await post(request, "/api/deals/" + deal.id + "/underwriting", {
    property_type:"single_family", expected_exit_price:240000, buyer_repairs:30000,
    buyer_funding_holding:8000, buyer_closing:5000, buyer_selling_costs:10000,
    buyer_minimum_profit:42000, target_assignment_fee:20000,
    owner_transaction_costs:4000, partner_payout_allowance:2000,
    contingency:2000, desired_owner_net:10000, basis:"Synthetic browser transaction assumptions"
  });
  await post(request, "/api/deals/" + deal.id + "/financial-plan", {
    seller_price:125000, assignment_fee:20000, planned_cash_at_risk:6000,
    max_cash_at_risk:10000, basis:"Synthetic browser transaction plan"
  });
  // Saving underwriting already advances a research deal to underwriting.
  await post(request, "/api/deals/" + deal.id + "/stage", {stage:"offer_decision", note:"Owner reviewing exact document"});

  await page.goto("/");
  await page.locator("#property-list").getByText(address, {exact:false}).click();
  const panel = page.locator(".transaction-workspace");
  await expect(panel).toContainText("Contracts, title & closing");
  await expect(panel).toContainText("Current signed document");
  await expect(panel).toContainText("Latest closing state");
  await expect(panel).toContainText("does not sign documents, clear title, move money");

  await panel.getByText("Save or revise a transaction document", {exact:true}).click();
  const form = panel.locator(".transaction-document-form");
  await form.locator('[name="document_kind"]').selectOption("purchase_agreement");
  await form.locator('[name="document_reference"]').fill("private://synthetic-agreement-v1.pdf");
  await form.locator('[name="version_reference"]').fill("synthetic-v1");
  await form.locator('[name="status"]').selectOption("reviewed");
  await form.locator('[name="signature_status"]').selectOption("not_signed");
  await form.locator('[name="title_status"]').selectOption("conditions_pending");
  await form.locator('[name="professional_review_reference"]').fill("synthetic-attorney-review");
  await form.locator('[name="closing_professional"]').fill("Synthetic Title Co");
  await form.locator('[name="note"]').fill("Reviewed synthetic purchase agreement");
  await form.locator('input[type="checkbox"]').check();
  await form.getByRole("button", {name:"Save transaction document"}).click();

  const history = panel.getByText("Document/version history", {exact:true});
  await history.click();
  await expect(panel).toContainText(/purchase agreement · reviewed/i);
  await expect(panel).toContainText("Current economics context");

  await panel.getByText("Save or revise a transaction document", {exact:true}).click();
  await form.locator('[name="document_reference"]').fill("private://synthetic-agreement-v2.pdf");
  await form.locator('[name="version_reference"]').fill("synthetic-v2");
  await form.locator('[name="status"]').selectOption("executed");
  await form.locator('[name="signature_status"]').selectOption("fully_signed");
  await form.locator('[name="professional_review_reference"]').fill("synthetic-attorney-review-v2");
  await form.locator('[name="note"]').fill("Executed synthetic purchase agreement");
  await form.locator('input[type="checkbox"]').check();
  await form.getByRole("button", {name:"Save transaction document"}).click();

  await expect(panel).toContainText("Current signed document");
  await expect(panel).toContainText("yes");
  await expect(panel).toContainText(/purchase agreement · executed/i);
  await expect(panel).toContainText(/signature: fully signed/i);

  await panel.getByText("Closing-state history", {exact:true}).click();
  await expect(panel).toContainText("No closing milestones recorded.");
  const recorded = await (await request.get("/api/state")).json();
  expect(recorded.transactions[deal.id].fully_signed_current_document).toBe(true);
  expect(recorded.transactions[deal.id].latest_closing_state).toBeNull();
  expect(recorded.deals.find(item=>item.id===deal.id).finance.summary.income).toBe(0);
});
