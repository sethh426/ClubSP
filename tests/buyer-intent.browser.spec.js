const {test, expect} = require('@playwright/test');
const unique = () => `${Date.now()}-${Math.random().toString(36).slice(2)}`;
async function capture(page, name, text, suffix) {
  await page.getByText('Add a post or a finding from elsewhere', {exact:true}).click();
  await page.getByLabel('Author or company').fill(name);
  await page.getByLabel('Public source URL').fill(`https://example.com/${suffix}`);
  await page.getByLabel('Relevant evidence').fill(text);
  await page.getByRole('button', {name:'Save finding', exact:true}).click();
  await expect(page.getByRole('status')).toContainText('Finding saved');
  return page.locator('.signal').filter({has:page.getByRole('heading',{name,exact:true})});
}
test('buyer finding keeps unknown age, escapes evidence, and prepares an unqualified prospect', async ({page}) => {
  await page.goto('/buyer-intent');
  const id = unique();
  const name = `Browser Buyer ${id}`;
  const card = await capture(page, name, "I'm buying houses in Fort Wayne. <img src=x onerror=alert(1)>", id);
  await expect(card).toContainText('Date unknown');
  await expect(card).toContainText('Self-reported buying intent');
  await expect(card.locator('img')).toHaveCount(0);
  await card.getByRole('button',{name:'Shortlist',exact:true}).click();
  await expect(card).toContainText('shortlisted');
  await page.reload();
  await expect(card).toContainText('shortlisted');
  await card.getByRole('button',{name:'Prepare buyer prospect'}).click();
  await expect(page).toHaveURL(/\/relationships#relationship-/);
  const relationships = await page.request.get('/api/relationships');
  const state = await relationships.json();
  const record = state.relationships.find(r => r.profile.name === name);
  expect(record.profile.permission).toBe('unknown');
  expect(record.profile.buyer_id).toBeNull();
  expect(record.qualification).toBeNull();
});
test('buyer solicitation cannot become a buyer prospect and dismissal is reversible', async ({page}) => {
  await page.goto('/buyer-intent');
  const id = unique();
  const card = await capture(page, `Browser Network ${id}`, 'Looking for cash buyers in Indiana. Join my buyer list.', id);
  await expect(card).toContainText('Looking for buyers');
  await expect(card.getByRole('button',{name:'Prepare buyer prospect'})).toHaveCount(0);
  await card.getByRole('button',{name:'Dismiss',exact:true}).click();
  await expect(card).toHaveCount(0);
  await page.getByRole('combobox', {name:'Show',exact:true}).selectOption('dismissed');
  await expect(card).toBeVisible();
  await card.getByRole('button',{name:'Restore',exact:true}).click();
  await page.getByRole('combobox', {name:'Show',exact:true}).selectOption('active');
  await expect(card).toBeVisible();
});
test('possible existing buyer contact has a visible review link without duplication', async ({page}) => {
  await page.goto('/buyer-intent');
  const id = unique(), name = `Existing Browser Buyer ${id}`;
  const card = await capture(page, name, 'We are buying homes in Indiana.', id);
  await card.getByRole('button',{name:'Prepare buyer prospect'}).click();
  await expect(page).toHaveURL(/\/relationships#relationship-/);
  await page.goto('/buyer-intent');
  const other = await capture(page, name, 'We are buying homes in Indiana.', id+'-other');
  const second = other.filter({has:page.getByRole('button',{name:'Prepare buyer prospect'})});
  await second.getByRole('button',{name:'Prepare buyer prospect'}).click();
  await expect(second.getByRole('link',{name:'Review saved contact'})).toBeVisible();
  await expect(page).toHaveURL(/\/buyer-intent$/);
});
