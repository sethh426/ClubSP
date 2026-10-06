const {test,expect}=require("@playwright/test");

test("revenue command center is read-only, ranked, and renders evidence as text", async ({page}) => {
  const payload = {
    execution_authorized:false,
    scope:"Read-only focus queue; no execution authority.",
    summary:{owner_review_deals:1,deal_actions:1,due_relationships:2,buyer_criteria_confirmations:1,buyer_criteria_reconfirmations:1,buyer_matched_candidates:1,focus_items:5},
    items:[
      {id:"deal:1",kind:"deal_owner_review",source:"Funding Desk + Opportunity Queue + Commitment Graph",
       title:"<img src=x onerror=window.injected=true>",status:"owner_review",priority_band:0,source_rank:0,
       next_action:"Owner review current terms",href:"/funding",opportunity_score:92,deal_readiness_score:84,
       criteria_fit_buyers:2,downside_net:12500,blockers:[]},
      {id:"relationship:1",kind:"buyer_criteria_confirmation",source:"Relationship Desk",title:"Synthetic Investor",
       status:"due",priority_band:2,source_rank:0,next_action:"Confirm current criteria",href:"/relationships#relationship-1",
       follow_up_on:"2026-10-05",blockers:[]},
      {id:"relationship:2",kind:"buyer_criteria_reconfirmation",source:"Relationship Desk",title:"Qualified Buyer",
       status:"due",priority_band:2,source_rank:1,next_action:"Reconfirm current criteria",href:"/relationships#relationship-2",
       follow_up_on:"2026-10-05",buyer_id:"buyer-2",qualification_id:"qualification-2",mandate_id:"mandate-2",blockers:[]},
      {id:"deal:2",kind:"deal_action",source:"Funding Desk + Opportunity Queue + Commitment Graph",
       title:"200 Blocked St",status:"needs_action",priority_band:3,source_rank:1,next_action:"Refresh evidence",
       href:"/funding",blockers:["Comparable evidence is stale"]},
      {id:"candidate:1",kind:"buyer_matched_candidate",source:"Commitment Graph + Sourcing",title:"300 Candidate Ave",
       status:"research_candidate",priority_band:4,source_rank:0,next_action:"Review source evidence",href:"/#sourcing-workspace",
       commitment_match_count:2,best_commitment_score:88,candidate_score:75,blockers:[]}
    ]
  };
  await page.route("**/api/command-center", route => route.fulfill({json:payload}));
  await page.goto("/command");
  await expect(page.getByRole("heading",{name:"What can move money forward now?"})).toBeVisible();
  await expect(page.locator("#command-summary")).toContainText("Owner-review deals1");
  await expect(page.locator("#command-summary")).toContainText("Buyer criteria to confirm1");
  await expect(page.locator("#command-summary")).toContainText("Buyer criteria to reconfirm1");
  const cards=page.locator(".command-item");
  await expect(cards).toHaveCount(5);
  await expect(cards.nth(0)).toContainText("<img src=x onerror=window.injected=true>");
  await expect(page.locator(".command-item img, .command-item script")).toHaveCount(0);
  await expect(cards.nth(0)).toContainText("Opportunity readiness 92/100");
  await expect(cards.nth(0)).toContainText("Close-path evidence 84/100");
  await expect(cards.nth(3)).toContainText("Comparable evidence is stale");
  await expect(page.getByText("no execution authority",{exact:false})).toBeVisible();
  expect(await page.evaluate(()=>window.injected)).toBeUndefined();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});

test("command center route is available from the property workspace", async ({page}) => {
  await page.goto("/");
  const link=page.getByRole("link",{name:/Revenue Command Center/});
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute("href","/command");
});
