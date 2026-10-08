const {test,expect} = require("@playwright/test");

test("confirmed request withdrawal and fresh reply renewal preserve one buyer",async({page,request})=>{
  const state=await (await request.get("/api/relationships")).json();
  const created=await request.post("/api/relationships",{data:{
    request_key:crypto.randomUUID(),name:"Synthetic Lifecycle Investor",company:"Synthetic only",
    email:`lifecycle-${crypto.randomUUID()}@example.test`,kind:"investor",status:"prospect",
    needs:"Confirm current criteria",source_reference:"synthetic-introduction",permission:"unknown",
    permission_reference:"",owner:"Owner",next_action:"Review criteria",markets:["Fort Wayne, IN"],
    follow_up_on:state.today,buyer_id:""
  }});
  expect(created.ok(),await created.text()).toBeTruthy();
  const rid=(await created.json()).relationship_id;
  const record=async()=> (await (await request.get("/api/relationships")).json()).relationships.find(r=>r.id===rid);
  const reply=async()=>{
    const row=await record();
    const result=await request.post(`/api/relationships/${rid}/interactions`,{data:{
      request_key:crypto.randomUUID(),profile_id:row.profile.id,event_id:row.event_id||"",
      direction:"incoming",outcome:"interested",note:"Synthetic actual reply: Fort Wayne single family assignments up to 150k, 30k repairs.",
      evidence_reference:"synthetic-reply",occurred_on:state.today,follow_up_on:"",next_action:"Review criteria"
    }});
    expect(result.ok(),await result.text()).toBeTruthy();
  };
  await reply();
  await page.goto("/relationships");
  const card=page.locator(`[data-relationship-id="${rid}"]`);
  const confirm=async(label,button)=>{
    await card.locator("summary").getByText(label,{exact:true}).click();
    const form=card.locator(".buyer-qualification-form");
    await form.getByLabel("Confirmed property types (one per line)",{exact:true}).fill("single_family");
    await form.getByLabel("Maximum total acquisition price",{exact:true}).fill("150000");
    await form.getByLabel("Maximum repairs",{exact:true}).fill("30000");
    await form.getByLabel("What the investor actually confirmed",{exact:true}).fill("Synthetic reviewed reply only");
    await form.getByLabel("I reviewed the latest incoming conversation",{exact:false}).check();
    await form.getByRole("button",{name:button,exact:true}).click();
    await expect(card).toContainText("Buyer request: current");
  };
  await confirm("Promote confirmed reply to buyer demand","Promote confirmed reply to buyer demand");
  const original=await record();
  await expect(card.getByText("Renew from a new buyer reply",{exact:true})).toHaveCount(0);
  await card.getByText("Record conversation / next step",{exact:true}).click();
  const eventForm=card.locator("form").filter({has:page.locator('[name="direction"]')});
  await eventForm.locator('[name="direction"]').selectOption("incoming");
  await eventForm.locator('[name="outcome"]').selectOption("buying_paused");
  await eventForm.getByLabel("Conversation evidence reference",{exact:true}).fill("synthetic-withdrawal");
  await eventForm.getByLabel("What happened / actual conversation",{exact:true}).fill("Synthetic: we have paused buying.");
  await eventForm.getByRole("button",{name:"Record interaction",exact:true}).click();
  await expect(card).toContainText("Buyer request: paused");
  expect((await record()).buyer.status).toBe("paused");
  await reply();await page.reload();
  await confirm("Renew from a new buyer reply","Renew confirmed buying request");
  const renewed=await record();
  expect(renewed.buyer.id).toBe(original.buyer.id);
  expect(renewed.qualification.mandate_id).not.toBe(original.qualification.mandate_id);
});

test("property-independent relationship, reply schedule and stop request",async({page,request},testInfo)=>{
  const unique=`${Date.now()}-${Math.random()}`;
  const errors=[];page.on("pageerror",error=>errors.push(error.message));
  await page.goto("/relationships");
  await page.getByText("Add a relationship",{exact:true}).click();
  const form=page.locator("#relationship-create-form form");
  await form.getByLabel("Name",{exact:true}).fill(`<img src=x onerror=alert(1)> ${unique}`);
  await form.getByLabel("Email",{exact:true}).fill(`synthetic-${unique}@example.test`);
  await form.getByLabel("Areas of interest (one per line)",{exact:true}).fill("Fort Wayne, IN");
  await form.getByLabel("Relationship source / evidence reference",{exact:true}).fill("Synthetic introduction only");
  await form.locator('[name="permission"]').selectOption("owner_reviewed");
  await form.getByLabel("Permission review reference",{exact:true}).fill("Synthetic permission only");
  const before=await (await request.get("/api/relationships")).json();
  await form.getByLabel("Next follow-up date",{exact:true}).fill(before.today);
  await form.getByLabel("Next action",{exact:true}).fill("Ask about current areas and timing");
  await form.getByRole("button",{name:"Save relationship",exact:true}).click();
  await expect(page.locator("#relationship-message")).toContainText("No message was sent");
  const after=await (await request.get("/api/relationships")).json();
  const record=after.relationships.find(r=>r.profile.email===`synthetic-${unique}@example.test`);
  expect(record).toBeTruthy();
  const card=page.locator(`[data-relationship-id="${record.id}"]`);
  await expect(card).toContainText("Follow-up due today");
  await expect(card.locator("img")).toHaveCount(0);
  await page.reload();await expect(card).toContainText("1 profile versions");
  await page.locator("#relationship-create").evaluate(node=>node.open=false);
  await page.screenshot({path:testInfo.outputPath("relationship-desk.png"),fullPage:true});
  await card.getByText("Message text for owner review",{exact:true}).click();
  const draftForm=card.locator(".relationship-draft form");
  await draftForm.getByLabel("Message subject",{exact:true}).fill("Synthetic priorities question");
  await draftForm.getByLabel("Editable message",{exact:true}).fill("SYNTHETIC ONLY: Which areas are you considering this month?");
  await draftForm.getByRole("button",{name:"Save message draft",exact:true}).click();
  await expect(card.getByText("Saved message drafts · 1",{exact:true})).toBeVisible();
  await page.reload();await expect(card.getByText("Saved message drafts · 1",{exact:true})).toBeVisible();
  await card.getByText("Saved message drafts · 1",{exact:true}).click();
  const saved=card.locator(".relationship-saved-drafts");
  await expect(saved).toContainText("Draft: pending");
  await expect(saved).toContainText("SYNTHETIC ONLY: Which areas are you considering this month?");
  await saved.getByLabel("Review notes / evidence",{exact:true}).fill("Synthetic identity and exact-text review");
  await saved.getByRole("button",{name:"Record draft review",exact:true}).click();
  await card.getByText("Saved message drafts · 1",{exact:true}).click();
  await expect(saved).toContainText("Draft: approved");
  await expect(saved.getByRole("button",{name:"Send approved email",exact:true})).toHaveCount(0);
  await expect(saved.getByText("authorize this send now",{exact:false})).toHaveCount(0);
  await saved.screenshot({path:testInfo.outputPath("reviewed-message.png")});
  await card.getByText("Record conversation / next step",{exact:true}).click();
  const eventForm=card.locator("form").first();
  await eventForm.locator('[name="direction"]').selectOption("incoming");
  await eventForm.getByLabel("Conversation evidence reference",{exact:true}).fill("Synthetic reply only");
  await eventForm.getByLabel("What happened / actual conversation",{exact:true}).fill("We buy nearby. Please send your criteria questions.");
  await eventForm.getByRole("button",{name:"Record interaction",exact:true}).click();
  await expect(card).toContainText("Next step not scheduled");
  await card.getByText("Saved message drafts · 1",{exact:true}).click();
  await expect(saved).toContainText("Draft: blocked");
  await expect(saved).toContainText("Relationship or conversation changed");
  await expect(saved.getByRole("button",{name:"Send approved email",exact:true})).toHaveCount(0);
  await expect(saved.locator('option[value="approved"]')).toHaveCount(0);
  await expect(saved.locator('[name="decision"]')).toHaveValue("rejected");
  await page.locator("#relationship-filter").selectOption("due");await expect(card).toBeHidden();
  await page.locator("#relationship-filter").selectOption("all");
  await card.getByText("Record conversation / next step",{exact:true}).click();
  await eventForm.locator('[name="direction"]').selectOption("incoming");
  await eventForm.getByLabel("Conversation evidence reference",{exact:true}).fill("Synthetic stop request only");
  await eventForm.getByLabel("What happened / actual conversation",{exact:true}).fill("Please stop emailing me.");
  await eventForm.getByRole("button",{name:"Record interaction",exact:true}).click();
  await expect(card).toContainText("Do not contact — recorded suppression");
  await expect(card.getByText("Message text for owner review",{exact:true})).toHaveCount(0);
  await page.locator("#relationship-filter").selectOption("blocked");await expect(card).toBeVisible();
  await card.screenshot({path:testInfo.outputPath("relationship-card.png")});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});


test("unknown investor permission requires explicit owner review before criteria outreach", async ({page,request}) => {
  const unique=`permission-${Date.now()}-${Math.random()}`;
  const before=await (await request.get("/api/relationships")).json();
  const created=await request.post("/api/relationships",{data:{
    request_key:crypto.randomUUID(),
    name:"Synthetic Permission Investor",
    company:"Synthetic Permission Co",
    email:`${unique}@example.test`,
    kind:"investor",
    status:"prospect",
    needs:"Confirm criteria only after permission review",
    source_reference:"https://example.test/public-contact",
    permission:"unknown",
    permission_reference:"",
    owner:"Owner",
    next_action:"Review contact permission",
    markets:["Fort Wayne, IN"],
    follow_up_on:before.today,
    buyer_id:"",
    relationship_id:"",
    profile_id:"",
    event_id:""
  }});
  expect(created.ok(),await created.text()).toBeTruthy();
  const saved=await created.json();

  await page.goto("/relationships");
  const card=page.locator(`[data-relationship-id="${saved.relationship_id}"]`);
  await expect(card).toContainText("No buyer linked");
  await expect(card.getByText("Review contact permission",{exact:true})).toBeVisible();
  await expect(card.getByText("Message text unavailable:",{exact:false})).toBeVisible();

  await card.getByText("Review contact permission",{exact:true}).click();
  const form=card.locator(".permission-review-form");
  await expect(form.getByText("Open recorded public source",{exact:true})).toHaveAttribute("href","https://example.test/public-contact");
  await form.locator('[name="decision"]').selectOption("allow_outreach");
  await form.locator('[name="review_note"]').fill("Synthetic owner review of public business-contact source and recipient identity.");
  await form.locator('input[type="checkbox"]').check();
  await form.getByRole("button",{name:"Record permission review",exact:true}).click();

  await expect(card.getByText("Review contact permission",{exact:true})).toHaveCount(0);
  await expect(card).toContainText("Confirm the investor's current buy box, funding evidence and closing capacity");
  await expect(card.getByText("Message text for owner review",{exact:true})).toBeVisible();

  const state=await (await request.get("/api/relationships")).json();
  const row=state.relationships.find(r=>r.id===saved.relationship_id);
  expect(row.profile.permission).toBe("owner_reviewed");
  expect(row.profile.permission_reference).toBe("https://example.test/public-contact");
  expect(row.buyer).toBeNull();
  expect(state.summary.qualified_buyers).toBe(before.summary.qualified_buyers);
});
