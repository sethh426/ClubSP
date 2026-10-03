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
