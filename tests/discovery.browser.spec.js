const { test, expect } = require("@playwright/test");
test("official notice checks are explicit and safely rendered", async ({ page }) => {
  let checks = 0;
  const source = {name:"Synthetic official notice", source_id:"accdc", url:"https://www.allencounty.in.gov/334/ACCDC-Properties", status:"no_inventory", excerpt:"<img src=x onerror=alert(1)>", candidates:[]};
  await page.route("**/api/discovery", route => route.fulfill({json:{sources:[source]}}));
  await page.route("**/api/discovery/check", async route => {
    checks++;
    expect(route.request().postDataJSON()).toEqual({source_id:"accdc"});
    await route.fulfill({json:{cached:false}});
  });
  await page.goto("/");
  await page.getByText("Official sale-notice discovery", {exact:true}).click();
  await expect(page.locator("#discovery-output")).toContainText("no inventory");
  await expect(page.locator("#discovery-output")).toContainText("<img src=x onerror=alert(1)>");
  await expect(page.locator("#discovery-output img")).toHaveCount(0);
  expect(checks).toBe(0);
  await page.getByRole("button", {name:"Reload saved checks", exact:true}).click();
  expect(checks).toBe(0);
  await page.getByRole("button", {name:"Check official notice", exact:true}).click();
  await expect(page.locator("#discovery-result")).toContainText("Check saved");
  expect(checks).toBe(1);
  const link = page.locator("#discovery-output a");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(await page.locator("#discovery-output").evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
});

test("saved notices stage a pending intake only after identity review", async ({ page }) => {
  let posted;
  const candidate = {address:"123 Example Road", minimum_bid:100000, availability:"Synthetic notice; verify availability",
    identity_note:"Review surveyed portions", bid_start:"2026-10-01T11:00:00-04:00", bid_end:"2026-11-30T11:00:00-05:00",
    parcel_ids:["02-01-01-101-001.000-001"], review_gaps:[], intake_blockers:[], buyer_criteria:[{name:"Synthetic buyer",status:"outside_recorded_criteria",reasons:["Advertised minimum bid exceeds buyer criteria"],funding_status_on_record:"unverified",commitment_confirmed:false}]};
  const source = {id:"synthetic-check", name:"Synthetic notice", source_id:"north_campus",
    url:"https://www.allencounty.in.gov/1305/Sale-of-North-Campus-Property", status:"advertised_window", candidates:[candidate]};
  await page.route("**/api/discovery", route => route.fulfill({json:{sources:[source]}}));
  await page.route("**/api/discovery/intake", async route => {
    posted = route.request().postDataJSON();
    await route.fulfill({json:{id:"synthetic-batch", row_id:"synthetic-row", status:"pending", duplicate:false}});
  });
  await page.goto("/");
  await page.getByText("Official sale-notice discovery", {exact:true}).click();
  await expect(page.locator("#discovery-output")).toContainText("outside recorded criteria");
  await page.getByText("Stage for identity review", {exact:true}).click();
  const form = page.locator("#discovery-output form");
  await form.getByLabel("Verified ZIP").fill("46818");
  await form.getByLabel("Reviewed property type").fill("land");
  await form.getByLabel("Reviewer", {exact:true}).fill("Synthetic reviewer");
  await form.getByLabel("Evidence and survey / parcel portion review notes").fill("Synthetic reviewed evidence");
  expect(posted).toBeUndefined();
  await form.getByLabel("I checked the address, market, selected parcel and surveyed portions").check();
  await form.getByRole("button", {name:"Stage pending intake"}).click();
  await expect(form).toContainText("No property or deal created");
  expect(posted.check_id).toBe("synthetic-check");
  expect(posted.candidate_index).toBe(0);
  expect(posted.identity_confirmed).toBe(true);
  expect(posted).not.toHaveProperty("minimum_bid");
  await expect(form.getByRole("link", {name:"Review staged intake"})).toHaveAttribute("href", "/?intake=synthetic-batch#sourcing-workspace");
});
test("stale notice blockers prevent staging a discovered notice", async ({ page }) => {
  const candidate = {address:"123 Example Road", minimum_bid:8000000, availability:"Synthetic notice",
    identity_note:"Review portions", bid_start:"2026-10-01", bid_end:"2026-11-30", parcel_ids:["synthetic"],
    review_gaps:[], within_recorded_price_limit:false, intake_blockers:["Official notice is stale; check the source again"]};
  await page.route("**/api/discovery", route => route.fulfill({json:{sources:[{
    name:"Synthetic notice", status:"advertised_window", url:"https://www.allencounty.in.gov/", candidates:[candidate]}]}}));
  await page.goto("/");
  await page.getByText("Official sale-notice discovery", {exact:true}).click();
  await page.getByText("Stage for identity review", {exact:true}).click();
  await expect(page.locator("#discovery-output")).toContainText("Official notice is stale");
  await expect(page.getByRole("button", {name:"Stage pending intake"})).toHaveCount(0);
});


test("sheriff-sale candidates render as research-only without treating judgment as price", async ({ page }) => {
  const candidate = {
    address:"3817 MARIGOLD DR", city:"Fort Wayne", state:"IN", zip:"46815",
    cause_number:"02D03-2505-MF-000199", sale_date:"2026-10-21",
    judgment_amount:72048.49, minimum_bid:null, intake_supported:false, parcel_ids:[],
    availability:"Scheduled sheriff sale notice; verify current status because sales may be cancelled or changed.",
    identity_note:"Sheriff notice does not provide parcel identity. Confirm the parcel independently before intake.",
    source_document_url:"https://www.allencountysheriff.org/example.pdf",
    review_gaps:["Confirm the exact parcel identity from Allen County records before intake."],
    intake_blockers:["Sheriff notice does not provide parcel identity; confirm the parcel before intake."],
    buyer_criteria:[{name:"Synthetic buyer",status:"needs_more_information",
      reasons:["No acquisition price is established; recorded judgment amount is not treated as a purchase price"],
      funding_status_on_record:"unverified",commitment_confirmed:false}],
  };
  await page.route("**/api/discovery", route => route.fulfill({json:{sources:[{
    id:"sheriff-check",name:"Allen County Sheriff mortgage foreclosure sales",
    source_id:"sheriff_sales",url:"https://www.allencountysheriff.org/2026-sheriff-sales/",
    status:"scheduled_sales",candidates:[candidate]
  }]}}));
  await page.goto("/");
  await page.getByText("Official sale-notice discovery", {exact:true}).click();
  const output=page.locator("#discovery-output");
  await expect(output).toContainText("Judgment amount (not a purchase price): $72,048.49");
  await expect(output).toContainText("Scheduled sheriff sale date: 2026-10-21");
  await expect(output).toContainText("Foreclosure cause: 02D03-2505-MF-000199");
  await output.getByText("Research only · parcel confirmation required", {exact:true}).click();
  await expect(output).toContainText("does not establish a parcel identity or purchase price");
  await expect(output.getByRole("button", {name:"Stage pending intake"})).toHaveCount(0);
  await expect(output.getByRole("link", {name:"Review official monthly sheriff-sale document"})).toHaveAttribute("rel","noopener noreferrer");
});


test("sheriff parcel resolution can unlock reviewed intake without inventing a price", async ({ page }) => {
  let resolved = 0;
  const unresolved = {
    address:"10324 GREEN OAK BLVD", city:"Fort Wayne", state:"IN", zip:"46814",
    cause_number:"02D03-2509-MF-000383", sale_date:"2026-10-21",
    judgment_amount:415715.93, minimum_bid:null, intake_supported:false, parcel_ids:[],
    availability:"Scheduled sheriff sale notice; verify current status.",
    identity_note:"Sheriff notice does not provide parcel identity.",
    review_gaps:["Confirm the exact parcel identity from Allen County records before intake."],
    intake_blockers:["Sheriff notice does not provide parcel identity; confirm the parcel before intake."],
    buyer_criteria:[],
  };
  const matched = {...unresolved, intake_supported:true,
    parcel_ids:["02-11-10-327-015.000-075"],
    identity_note:"Parcel identity resolved against Allen County GIS site-address and parcel services. Owner/title review is still required.",
    parcel_resolution:{status:"resolved",official_address:"10324 GREENOAK BLVD",pin:"021110327015000075",
      gis_id:"02-11-10-327-015.000-075",property_class:"1 Family Dwell - Platted Lot",year_built:1998},
    intake_blockers:[],
    review_gaps:["Parcel identity was resolved from Allen County GIS; independently confirm title, ownership and legal description before action."]
  };
  let current=unresolved;
  await page.route("**/api/discovery", route => route.fulfill({json:{sources:[{
    id:"sheriff-check",name:"Allen County Sheriff mortgage foreclosure sales",source_id:"sheriff_sales",
    url:"https://www.allencountysheriff.org/2026-sheriff-sales/",status:"scheduled_sales",
    candidates:[current],parcel_resolution_count:resolved
  }]}}));
  await page.route("**/api/discovery/resolve-parcels", async route => {
    expect(route.request().postDataJSON()).toEqual({check_id:"sheriff-check"});
    resolved=1; current=matched;
    await route.fulfill({json:{id:"sheriff-check",status:"scheduled_sales",candidates:[matched],parcel_resolution_count:1}});
  });
  await page.goto("/");
  await page.getByText("Official sale-notice discovery", {exact:true}).click();
  const output=page.locator("#discovery-output");
  await output.getByRole("button", {name:"Resolve parcel IDs from Allen County GIS"}).click();
  await expect(output).toContainText("Official GIS match: 10324 GREENOAK BLVD");
  await expect(output).toContainText("PIN 021110327015000075");
  await expect(output).toContainText("Parcels: 02-11-10-327-015.000-075");
  await expect(output).toContainText("Judgment amount (not a purchase price)");
  await expect(output.getByText("Stage for identity review", {exact:true})).toHaveCount(1);
});
