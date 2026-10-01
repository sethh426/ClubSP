"use strict";

// Form request IDs must also work on a private HTTP address over WireGuard.
function requestUUID() {
  const source = globalThis.crypto;
  if (typeof source?.randomUUID === "function") return source.randomUUID();
  if (typeof source?.getRandomValues !== "function") {
    throw new Error("This browser cannot generate secure request IDs");
  }
  const bytes = source.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16),
          hex.slice(16, 20), hex.slice(20)].join("-");
}


function workspaceField(form, name, label, type = "text", value = "", required = true) {
  const wrap = node("label", label);
  const input = node(type === "textarea" ? "textarea" : "input");
  input.name = name;
  if (type !== "textarea") input.type = type;
  input.value = value;
  input.required = required;
  if (type === "number") { input.min = "0"; input.step = "0.01"; }
  if (type === "text") input.maxLength = 500;
  if (type === "textarea") input.maxLength = 1000;
  wrap.append(input); form.append(wrap);
  return input;
}
function workspaceSelect(form, name, label, options, value) {
  const wrap = node("label", label), select = node("select"); select.name = name;
  options.forEach(([id, text]) => { const option = node("option", text); option.value = id; select.append(option); });
  if (value !== undefined) select.value = value;
  wrap.append(select); form.append(wrap); return select;
}
function workspaceSubmit(form, label) {
  const button = node("button", label, "button"); button.type = "submit"; form.append(button); return button;
}
function workspaceDetails(title, open = false) {
  const details = node("details", undefined, "workspace-details"); details.open = open;
  details.append(node("summary", title)); return details;
}
function renderScorecard() {
  const box = $("money-scorecard"); box.replaceChildren();
  const cards = [
    ["Reconciled contribution", amount("money", state.scorecard.reconciled_net_contribution)],
    ["Unrecovered cash", amount("money", state.scorecard.unrecovered_cash)],
    ["Ended deals to reconcile", state.scorecard.unreconciled_ended_deals],
    ["Open exceptions / overdue", state.scorecard.open_exceptions + " / " + state.scorecard.overdue_tasks],
  ];
  cards.forEach(([label,value]) => { const card=node("article");card.append(node("span",label),node("strong",String(value)));box.append(card); });
  const guide = $("operations-catalog"); guide.replaceChildren();
  state.operations.forEach(op => { const d=workspaceDetails(String(op.number).padStart(2,"0")+" / "+op.name+" · "+op.capability);d.append(node("p",op.expectation,"muted small"));guide.append(d); });
}
function renderFinance(deal, box) {
  const finance = deal.finance, s = finance.summary;
  const panel = node("section", undefined, "panel finance-workspace");
  panel.append(node("p","OPERATION 16 / DEAL MONEY","eyebrow"),node("h2","Cash & profit"));
  const totals = node("div", undefined, "scenario-grid");
  [["Money received",s.income],["Costs recorded",s.paid_expenses+s.escrow_applied+s.escrow_forfeited],
   ["Escrow still held",s.escrow_held],["Recorded contribution",s.net_contribution]].forEach(([label,val])=>{const c=node("article",undefined,"scenario-card");c.append(node("span",label),node("strong",amount("money",val)));totals.append(c);});
  panel.append(totals,node("p","Contribution is pre-tax and excludes business overhead unless you record its allocation. A complete reconciliation requires your evidence and confirmation.","muted small"));
  panel.append(node("p","Cash at risk now: "+amount("money",s.unrecovered_cash)+" · Planned peak: "+(finance.projected_cash_at_risk===null?"not recorded":amount("money",finance.projected_cash_at_risk)),"finance-risk"));
  if(finance.contract_blockers.length){const gaps=node("ul",undefined,"blocker-list");finance.contract_blockers.forEach(x=>gaps.append(node("li",x)));panel.append(gaps);}
  const ended = ["completed","lost"].includes(deal.stage);
  const plan = finance.plan;
  if(!ended){
    const details=workspaceDetails("Proposed terms & owner cash limit",!plan);
    details.append(node("p","Enter the total peak cash you expect to have unrecovered before closing, including deposits, acquisition and costs you fund. Document any financing assumptions.","muted small"));
    const form=node("form",undefined,"money-plan-form workspace-form");
    [ ["seller_price","Proposed seller price ($)"], ["assignment_fee","Planned assignment fee ($; resale uses 0)"],
      ["planned_cash_at_risk","Planned peak owner cash at risk ($)"], ["max_cash_at_risk","Owner cash-at-risk limit ($)"]
    ].forEach(([name,label])=>workspaceField(form,name,label,"number",plan?.[name]??(name==="assignment_fee"&&deal.strategy==="resale"?0:"")));
    workspaceField(form,"basis","Terms, financing and cash assumptions","textarea",plan?.basis||"");
    workspaceSubmit(form,"Save proposed terms");
    form.addEventListener("submit",e=>{e.preventDefault();runForm(form,()=>api("/api/deals/"+deal.id+"/financial-plan",values(form)),"Proposed terms and cash limit saved.");});
    details.append(form);panel.append(details);
  }
  if(plan){
    const grid=node("div",undefined,"scenario-grid fixed-forecasts");
    Object.entries(plan.forecasts).forEach(([key,f])=>{const c=node("article",undefined,"scenario-card");c.append(node("h3",key.toUpperCase()+" AT YOUR PRICE"),node("p","Seller price: "+amount("money",f.seller_price)),node("p","Net contribution: "+amount("money",f.net_contribution)),node("span",f.supports_entered_terms?"Supports entered terms":"Terms need review","pill"+(f.supports_entered_terms?"":" open")));grid.append(c);});
    panel.append(grid);
  }
  const entryDetails=workspaceDetails("Record actual cash movement",!s.active_entry_ids.length);
  entryDetails.append(node("p","Record cash expenses excluding deposits. When earnest money is used at settlement, record its escrow application separately from the remaining cash purchase payment.","muted small"));
  const entryForm=node("form",undefined,"ledger-form workspace-form");
  const kinds=[["expense","Expense cash paid"],["income","Money actually received"],["escrow_deposit","Earnest money deposited"],["escrow_return","Escrow refunded"],["escrow_applied","Escrow applied to purchase"],["escrow_forfeit","Escrow forfeited"]];
  const kind=workspaceSelect(entryForm,"kind","Movement",kinds);
  const categories={expense:["purchase","repairs","funding_holding","closing","selling","transaction","partner_payout","other"],income:["assignment_fee","resale_proceeds","other_receipt"],escrow_deposit:["earnest_money"],escrow_return:["earnest_money"],escrow_applied:["purchase"],escrow_forfeit:["earnest_money_loss"]};
  const category=workspaceSelect(entryForm,"category","Category",categories.expense.map(x=>[x,readable(x)]));
  kind.addEventListener("change",()=>{category.replaceChildren();categories[kind.value].forEach(x=>{const o=node("option",readable(x));o.value=x;category.append(o);});});
  workspaceField(entryForm,"amount","Amount ($)","number");
  workspaceField(entryForm,"occurred_on","Cash date","date",state.today);
  workspaceField(entryForm,"evidence_reference","Receipt, bank or settlement reference");
  workspaceField(entryForm,"note","What this movement records","textarea");
  workspaceSubmit(entryForm,"Record cash movement");
  const entryKey=requestUUID();
  entryForm.addEventListener("submit",e=>{e.preventDefault();runForm(entryForm,()=>api("/api/deals/"+deal.id+"/ledger",{...values(entryForm),entry_key:entryKey}),"Cash movement recorded.");});
  entryDetails.append(entryForm);panel.append(entryDetails);
  const history=node("div",undefined,"ledger-history");
  const active=new Set(s.active_entry_ids);
  finance.entries.forEach(entry=>{
    const row=node("article",undefined,"evidence-row");
    row.append(node("strong",(entry.reversal_of?"Reversal · ":"")+readable(entry.kind)+" · "+amount("money",entry.amount_cents/100)),node("p",entry.occurred_on+" · "+readable(entry.category)+" · "+entry.note,"small"),node("p","Evidence: "+entry.evidence_reference,"muted small"));
    if(active.has(entry.id)){
      const d=workspaceDetails("Correct this entry");const f=node("form",undefined,"workspace-form reversal-form");
      workspaceField(f,"note","Correction reason");workspaceField(f,"evidence_reference","Correction evidence reference");workspaceSubmit(f,"Reverse entry");
      const key=requestUUID();
      f.addEventListener("submit",e=>{e.preventDefault();runForm(f,()=>api("/api/deals/"+deal.id+"/ledger",{...values(f),kind:entry.kind,category:entry.category,amount:String(entry.amount_cents/100),occurred_on:state.today,entry_key:key,reversal_of:entry.id}),"Reversal recorded. Add the corrected movement if needed.");});d.append(f);row.append(d);
    }else row.append(node("span",entry.reversal_of?"Audit reversal":"Reversed","pill"));
    history.append(row);
  });panel.append(history);
  const recon=finance.reconciliation;
  if(recon){panel.append(node("p",(recon.current?"Reconciled":"Reconciliation needs refresh")+" · actual "+amount("money",recon.actual_net)+" · forecast "+(recon.forecast_net===null?"not recorded":amount("money",recon.forecast_net))+" · variance "+(recon.variance===null?"n/a":amount("money",recon.variance)),"reconciliation-status note"));}
  if(ended&&(!recon||!recon.current)){
    const f=node("form",undefined,"reconciliation-form workspace-form");
    workspaceField(f,"note","Reconciliation / failure notes","textarea");workspaceField(f,"evidence_reference","Complete settlement and cost reference");
    const label=node("label",undefined,"check-label"),check=node("input");check.type="checkbox";check.required=true;label.append(check,document.createTextNode(" I confirmed all costs, receipts and escrow dispositions are recorded"));f.append(label);
    workspaceSubmit(f,"Reconcile deal");f.addEventListener("submit",e=>{e.preventDefault();runForm(f,()=>api("/api/deals/"+deal.id+"/reconciliation",{...values(f),owner_confirmed_complete:check.checked}),"Deal profit reconciled.");});panel.append(f);
  }
  box.append(panel);
}
function renderOperations(deal, box) {
  const panel=node("section",undefined,"panel operations-workspace");panel.append(node("h2","Operations & exceptions"));
  const details=workspaceDetails("Add task or exception");const form=node("form",undefined,"task-form workspace-form");
  workspaceField(form,"title","Task / exception");workspaceField(form,"owner","Responsible owner","text","Owner");workspaceField(form,"expected_result","Expected result","textarea");
  workspaceSelect(form,"operation","Operation",state.operations.map(o=>[String(o.number),String(o.number).padStart(2,"0")+" / "+o.name]));
  workspaceSelect(form,"kind","Type",[["task","Task"],["exception","Exception"]]);
  workspaceSelect(form,"blocking_stage","Block a stage until resolved",[["","Track only"],["any","Any forward stage"],["contracted","Contracted"],["closing","Closing"],["completed","Completed"]]);
  workspaceField(form,"due_on","Due date (optional)","date","",false);workspaceSubmit(form,"Save operation task");
  form.addEventListener("submit",e=>{e.preventDefault();const data=values(form);data.operation=Number(data.operation);runForm(form,()=>api("/api/deals/"+deal.id+"/tasks",data),"Operation task saved.");});details.append(form);panel.append(details);
  const list=node("div",undefined,"operation-tasks");
  deal.tasks.forEach(task=>{
    const card=node("article",undefined,"evidence-row"+(task.overdue?" overdue":""));
    const head=node("div",undefined,"row-head");head.append(node("strong","Operation "+String(task.operation).padStart(2,"0")+" · "+task.title),node("span",task.status+(task.overdue?" · overdue":""),"pill"));card.append(head);
    card.append(node("p","Owner: "+task.owner+" · Due: "+(task.due_on||"not set")+(task.blocking_stage?" · Blocks: "+task.blocking_stage:""),"muted small"),node("p",task.expected_result,"small"));
    const last=task.events.at(-1);if(last)card.append(node("p",last.note+(last.evidence_reference?" · Evidence: "+last.evidence_reference:""),"small"));
    if(!["underwriting","money_plan","reconcile"].includes(task.system_key)){
      const d=workspaceDetails(task.status==="open"?"Record completion":"Reopen task");const f=node("form",undefined,"workspace-form task-resolution-form");
      workspaceField(f,"note","Result / reason");workspaceField(f,"evidence_reference","Evidence reference","text","",task.status==="open");workspaceSubmit(f,task.status==="open"?"Complete task":"Reopen task");
      f.addEventListener("submit",e=>{e.preventDefault();runForm(f,()=>api("/api/tasks/"+task.id+"/status",{...values(f),status:task.status==="open"?"done":"open"}),"Operation task updated.");});d.append(f);card.append(d);
    }
    const schedule=workspaceDetails("Owner & deadline");const sf=node("form",undefined,"workspace-form task-schedule-form");
    workspaceField(sf,"owner","Responsible owner","text",task.owner);workspaceField(sf,"due_on","Due date (optional)","date",task.due_on,false);workspaceField(sf,"note","Scheduling note");workspaceSubmit(sf,"Save owner and deadline");
    sf.addEventListener("submit",e=>{e.preventDefault();runForm(sf,()=>api("/api/tasks/"+task.id+"/schedule",values(sf)),"Owner and deadline saved.");});schedule.append(sf);card.append(schedule);list.append(card);
  });panel.append(list);
  const audit=workspaceDetails("Deal stage history");deal.events.forEach(e=>audit.append(node("p",readable(e.stage_before)+" → "+readable(e.stage_after)+" · "+e.note+" · "+date(e.created_at),"small")));panel.append(audit);box.append(panel);
}

function renderResearch() {
  const box=$("property-research");box.replaceChildren();
  const provider=state.providers[0];
  box.append(node("p","OPERATIONS 05–06 / OFFICIAL RECORDS","eyebrow"),node("h2","Research a parcel"));
  box.append(node("p",provider.name+" · exact-key lookup · "+provider.daily_request_limit+" requests/day · "+provider.cache_hours+"h cache","muted small"));
  const link=node("a","Open official iMap portal");link.href=provider.home;link.target="_blank";link.rel="noopener noreferrer";box.append(link);
  box.append(node("p",provider.limits,"muted small"));
  const f=node("form",undefined,"parcel-research-form workspace-form");
  const key=workspaceField(f,"parcel_key","Allen County parcel key (18 digits; hyphens allowed)");key.maxLength=30;key.placeholder="02-…";
  workspaceSubmit(f,"Look up official record");
  f.addEventListener("submit",e=>{e.preventDefault();runForm(f,()=>api("/api/properties/"+selected+"/research",values(f)),"Lookup saved. Review the returned identity before accepting evidence.");});box.append(f);
  const snapshots=state.research.filter(s=>s.property_id===selected);
  snapshots.forEach(snapshot=>{
    const card=node("article",undefined,"evidence-row research-snapshot");
    card.append(node("strong",snapshot.parcel_key+" · "+readable(snapshot.status)),node("p","Retrieved: "+date(snapshot.created_at),"muted small"));
    if(snapshot.error)card.append(node("p",snapshot.error,"note"));
    if(snapshot.status==="no_match")card.append(node("p","No exact parcel record was returned. Confirm the key in the official portal.","muted small"));
    if(snapshot.status==="ambiguous")card.append(node("p","Multiple records were returned. Resolve identity using the official record source before importing.","muted small"));
    if(snapshot.record){
      Object.entries(snapshot.record).forEach(([key,value])=>card.append(node("p",readable(key)+": "+value,"small")));
      Object.entries(snapshot.identity).forEach(([key,value])=>card.append(node("p",readable(key)+" · entered: "+value.entered+" · reported: "+(value.reported||"unknown")+" · "+(value.matches?"matches normalized text":"requires identity review"),"muted small")));
    }
    if(snapshot.status==="pending"){
      const form=node("form",undefined,"research-review-form workspace-form");
      workspaceSelect(form,"decision","Review decision",[["accept","Accept as sourced evidence"],["reject","Reject this match"]]);
      workspaceField(form,"note","Review basis / discrepancy notes","textarea");
      const needsExplanation=Object.values(snapshot.identity).some(v=>!v.matches);
      if(needsExplanation)workspaceField(form,"mismatch_explanation","Explain the identity mismatch before accepting","textarea","",false);
      const confidence=workspaceField(form,"confidence","Your evidence confidence (0–1)","number","0.8");confidence.max="1";
      const label=node("label",undefined,"check-label"),check=node("input");check.type="checkbox";label.append(check,document.createTextNode(" I compared and confirmed this parcel is the selected property"));form.append(label);
      workspaceSubmit(form,"Save source review");form.addEventListener("submit",e=>{e.preventDefault();const v=values(form);v.owner_confirmed_identity=check.checked;v.confidence=Number(v.confidence);runForm(form,()=>api("/api/research/"+snapshot.id+"/review",v),"Source review recorded.");});card.append(form);
    }
    if(snapshot.review_note)card.append(node("p","Review: "+snapshot.review_note,"small"));
    if(snapshot.status==="accepted")card.append(node("p",snapshot.fact_ids.length+" sourced facts accepted; original history retained.","muted small"));
    box.append(card);
  });
}
