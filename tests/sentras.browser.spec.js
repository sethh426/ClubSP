const { test, expect } = require('@playwright/test');
test('evidence planning and collection show unavailable coverage without inventing records', async ({page}) => {
  await page.goto('/sentras');
  await expect(page.getByRole('heading', {name:'Evidence Compiler'})).toBeVisible();
  await page.getByLabel('Parcel or record identity').fill('synthetic-A1');
  await page.getByLabel('County and state').fill('Synthetic County');
  await page.getByLabel('Evidence goals (comma separated)').fill('parcel_identity');
  await page.getByRole('button', {name:'Preview evidence plan'}).click();
  await expect(page.locator('#plan')).toContainText('"satisfiable": false');
  await page.getByRole('button', {name:'Collect evidence', exact:true}).click();
  await expect(page.locator('#status')).toContainText('Evidence run: incomplete');
  await expect(page.locator('#runs')).toContainText('synthetic-A1');
  await page.reload();
  await expect(page.locator('#runs')).toContainText('synthetic-A1');
});
