const { test, expect } = require('@playwright/test');
const { randomUUID } = require('node:crypto');

test('save economic review, show references safely and invalidate changed terms', async ({page, request}) => {
  const suffix = randomUUID(), address = 'Synthetic economic ' + suffix;
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  const post = async (path, data) => { const response = await request.post(path, {data}); expect(response.ok()).toBeTruthy(); return response.json(); };
  const prop = await post('/api/properties', {address, city: 'Fort Wayne', state: 'IN'});
  const today = new Date().toISOString().slice(0,10);
  for (const [attribute,value] of Object.entries({parcel_id: 'subject-'+suffix, property_type:'single_family', property_class:'Residential', neighborhood_code:'001', living_area:1800, year_built:1980, bath:2, acreage:0.2})) {
    await post('/api/facts', {property_id:prop.id, attribute,value,provider:'Synthetic',confidence:0.9});
  }
  const batch = await post('/api/sourcing/import', {kind:'county_sales', provider:'Synthetic', source_url:'https://example.com', source_date:today, rights_basis:'Synthetic fixture', city:'Fort Wayne',state:'IN', csv:'Parcel Number,Address,Sale Date,Sale Price,Living Area,Property Class,Neighborhood Code,Year Built,Bath,Acreage\ncomp-'+suffix+',Synthetic comparable,'+today+',240000,1800,Residential,001,1980,2,0.2'});
  const state = await (await request.get('/api/state')).json();
  const row = state.sourcing.rows.find(r=>r.batch_id===batch.id);
  await post('/api/sourcing/rows/'+row.id+'/review', {action:'accept', property_id:prop.id, reviewer:'Synthetic', note:'Reviewed', evidence_reference:'Synthetic',identity_confirmed:true,sale_verified:true});
  const deal = await post('/api/deals', {property_id:prop.id,strategy:'assignment'});
  await post('/api/deals/'+deal.id+'/underwriting', {property_type:'single_family', expected_exit_price:240000,buyer_repairs:30000,buyer_funding_holding:8000,buyer_closing:5000,buyer_selling_costs:10000,buyer_minimum_profit:42000,target_assignment_fee:20000,owner_transaction_costs:4000,partner_payout_allowance:2000,contingency:2000,desired_owner_net:10000,basis:'Synthetic'});
  const terms={seller_price:80000,assignment_fee:20000,planned_cash_at_risk:6000,max_cash_at_risk:10000,basis:'Synthetic'};
  await post('/api/deals/'+deal.id+'/financial-plan', terms);
  await post('/api/opportunities/policy', {markets:['Fort Wayne, IN'],strategies:['assignment'],property_types:['single_family'],max_seller_price:150000,max_deal_cash_at_risk:10000,max_portfolio_cash_at_risk:20000,min_downside_net:0,evidence_max_age_days:30,basis:'Synthetic'});
  await page.goto('/'); await expect(page.locator('#connection')).toHaveText('Saved locally');
  const card=page.locator('.opportunity-card').filter({hasText:address});
  await card.evaluate(element => { let parent=element.parentElement; while(parent){if(parent.tagName==='DETAILS')parent.open=true; parent=parent.parentElement;} });
  await expect(card.locator('.economic-screen')).toContainText('evidence review required');
  const form=card.locator('.economic-review-form');
  const reference='Synthetic <img src=x onerror=window.injected=true>';
  for (const name of ['reviewer','exit_price_reference','repair_reference','funding_cost_reference','closing_selling_reference','owner_cost_partner_reference','condition_concessions_reference','note']) await form.locator('[name="'+name+'"]').fill(reference);
  await form.locator('input[type=checkbox]').check();
  await form.getByRole('button',{name:'Save economic evidence review'}).click();
  await expect(page.locator('#message')).toContainText('Economic evidence review saved');
  await expect(card.locator('.economic-review-status')).toContainText('Current owner review');
  await post('/api/deals/'+deal.id+'/financial-plan',{...terms,seller_price:81000});
  await page.reload(); await expect(page.locator('#connection')).toHaveText('Saved locally');
  await card.evaluate(element => { let parent=element.parentElement; while(parent){if(parent.tagName==='DETAILS')parent.open=true; parent=parent.parentElement;} });
  await expect(card.locator('.economic-review-status')).toContainText('Previous review needs refresh');
  await expect(card.locator('.economic-review-form')).toHaveCount(1);
  expect(await card.locator('img').count()).toBe(0);
  expect(await page.evaluate(()=>window.injected)).toBeUndefined();
  expect(errors).toEqual([]);
});
