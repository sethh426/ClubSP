"use strict";
const $ = (id) => document.getElementById(id);
let state = null;
let selected = null;
let selectedDeal = null;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function message(text, error = false) {
  $("message").textContent = text;
  $("message").className = "message" + (error ? " error" : "");
  $("message").hidden = false;
}
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed");
  return result;
}
function values(form) {
  return Object.fromEntries(new FormData(form));
}
function readable(kind) { return kind.replaceAll("_", " "); }
function amount(kind, value) {
  return kind === "days_to_close" ? value + " days" :
    new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
}
function date(value) { return new Date(value).toLocaleString(); }

function renderList() {
  const list = $("property-list");
  list.replaceChildren();
  const query = $("search").value.toLowerCase().trim();
  const properties = state.properties.filter(p =>
    (p.address + " " + p.city + " " + p.state).toLowerCase().includes(query));
  if (!properties.length) list.append(node("p", query ? "No matching properties." : "Your first property belongs here.", "muted small"));
  properties.forEach(property => {
    const button = node("button", undefined, "property-item" + (property.id === selected ? " selected" : ""));
    button.type = "button";
    button.setAttribute("aria-pressed", String(property.id === selected));
    button.append(node("strong", property.address), node("span", property.city + ", " + property.state));
    button.addEventListener("click", () => { selected = property.id; render(); });
    list.append(button);
  });
}
function renderFacts() {
  const box = $("facts");
  box.replaceChildren();
  const facts = state.facts.filter(f => f.subject_id === selected).reverse();
  if (!facts.length) box.append(node("p", "No sourced facts yet.", "muted small"));
  const superseded = new Set(facts.map(f => f.supersedes_fact_id).filter(Boolean));
  facts.forEach(fact => {
    const source = state.sources.find(s => s.id === fact.source_id);
    const row = node("article", undefined, "evidence-row");
    const head = node("div", undefined, "row-head");
    head.append(node("strong", readable(fact.attribute) + ": " + String(fact.value)));
    head.append(node("span", superseded.has(fact.id) ? "Historical" : "Confidence " + Math.round(fact.confidence * 100) + "%", "pill"));
    row.append(head);
    const detail = node("p", "Source: " + (source ? source.provider : "Unavailable") + " · " + date(fact.observed_at));
    if (source && source.url) {
      const link = node("a", " View source");
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      detail.append(link);
    }
    row.append(detail);
    box.append(row);
  });
}
function inputLabel(label, name, type, placeholder) {
  const wrapper = node("label", label);
  const input = node("input");
  input.name = name; input.type = type; input.required = true;
  if (placeholder) input.placeholder = placeholder;
  wrapper.append(input);
  return { wrapper, input };
}
function renderPredictions() {
  const box = $("predictions");
  box.replaceChildren();
  const predictions = state.predictions.filter(p => p.subject_id === selected).reverse();
  if (!predictions.length) box.append(node("p", "No estimates yet.", "muted small"));
  predictions.forEach(prediction => {
    const card = node("article", undefined, "estimate-card");
    const head = node("div", undefined, "row-head");
    head.append(node("h3", readable(prediction.prediction_type)), node("span", prediction.resolved_at ? "Outcome recorded" : "Awaiting outcome", "pill" + (prediction.resolved_at ? "" : " open")));
    card.append(head);
    const summary = node("div", undefined, "estimate-values");
    summary.append(node("span", "Estimated " + amount(prediction.prediction_type, prediction.predicted_value)),
      node("span", "Confidence " + Math.round(prediction.confidence * 100) + "%"));
    if (prediction.resolved_at) {
      summary.append(node("span", "Actual " + amount(prediction.prediction_type, prediction.actual_value)),
        node("span", "Error " + (prediction.error === null ? "n/a" : Math.round(prediction.error * 100) + "%")));
      card.append(summary);
      card.append(node("p", "Recorded " + date(prediction.resolved_at), "muted small"));
    } else {
      card.append(summary);
      const form = node("form", undefined, "outcome-form");
      const actual = inputLabel("Actual value", "actual_value", "number");
      actual.input.min = "0"; actual.input.step = "0.01";
      const source = inputLabel("Outcome source", "provider", "text", "Invoice, closing record…");
      source.input.maxLength = 120;
      const confidence = inputLabel("Confidence", "confidence", "number");
      confidence.input.min = "0"; confidence.input.max = "1"; confidence.input.step = "0.01"; confidence.input.value = "1";
      const submit = node("button", "Record outcome", "button");
      submit.type = "submit";
      form.append(actual.wrapper, source.wrapper, confidence.wrapper, submit);
      form.addEventListener("submit", event => {
        event.preventDefault();
        const data = values(form);
        data.actual_value = Number(data.actual_value);
        data.confidence = Number(data.confidence);
        runForm(form, () => api("/api/predictions/" + prediction.id + "/outcome", data), "Outcome recorded. Learning history updated.");
      });
      card.append(form);
    }
    box.append(card);
  });
}
function renderLearning() {
  const box = $("learning");
  box.replaceChildren();
  const predictionIds = new Set(state.predictions.filter(p => p.subject_id === selected).map(p => p.id));
  const records = state.learning_records.filter(l => l.evidence_refs.some(id => predictionIds.has(id))).reverse();
  if (!records.length) box.append(node("p", "Learning records appear after you record an outcome.", "muted small"));
  records.forEach(record => {
    const row = node("article", undefined, "learning-row");
    row.append(node("strong", readable(record.domain)), node("p", record.pattern));
    row.append(node("p", "Evidence records: " + record.evidence_refs.length + " · Quality score " + Math.round(record.confidence * 100) + "% · " + date(record.learned_at)));
    box.append(row);
  });
}
function renderDealBoard() {
  const box = $("deal-board"); box.replaceChildren();
  const deals = state.deals.filter(d => d.property_id === selected);
  if (!deals.length) { box.append(node("p", "No deal started for this property.", "muted small")); return; }
  if (!deals.some(d => d.id === selectedDeal)) selectedDeal = deals[0].id;
  const picker = node("div", undefined, "deal-tabs");
  deals.forEach(deal => {
    const b = node("button", deal.strategy + " · " + deal.stage.replaceAll("_", " "), "button secondary");
    b.type = "button"; b.setAttribute("aria-pressed", String(deal.id === selectedDeal));
    b.addEventListener("click", () => { selectedDeal = deal.id; renderDealBoard(); });
    picker.append(b);
  });
  box.append(picker);
  const deal = deals.find(d => d.id === selectedDeal);
  const stageForm = node("form", undefined, "inline-form");
  const stageWrap = node("label", "Move stage");
  const stage = node("select"); stage.name = "stage"; stage.required = true;
  ["contacting","qualified","underwriting","offer_decision","contracted","disposition","closing","completed","lost"].forEach(s => { if (s !== deal.stage) { const o=node("option",s.replaceAll("_"," ")); o.value=s; stage.append(o); } });
  stageWrap.append(stage);
  const noteWrap=node("label","Reason / note"); const note=node("input"); note.name="note"; note.required=true; note.maxLength=1000; noteWrap.append(note);
  const refWrap=node("label","Evidence reference"); const ref=node("input"); ref.name="evidence_reference"; ref.maxLength=500; ref.placeholder="Document or record ID"; refWrap.append(ref);
  const signedWrap=node("label"); const signed=node("input"); signed.type="checkbox"; signed.name="owner_confirmed_signed"; signedWrap.append(signed,document.createTextNode(" I confirmed signed agreement"));
  const closedWrap=node("label"); const closed=node("input"); closed.type="checkbox"; closed.name="owner_confirmed_closed"; closedWrap.append(closed,document.createTextNode(" I confirmed closing"));
  const submit=node("button","Save stage","button"); submit.type="submit";
  stageForm.append(stageWrap,noteWrap,refWrap,signedWrap,closedWrap,submit);
  stageForm.addEventListener("submit",e=>{e.preventDefault();const v=values(stageForm);v.owner_confirmed_signed=signed.checked;v.owner_confirmed_closed=closed.checked;runForm(stageForm,()=>api("/api/deals/"+deal.id+"/stage",v),"Deal stage recorded.");});
  box.append(stageForm);
  const underwriting=node("form",undefined,"underwrite-form");
  const fields=[["property_type","Property type","single_family","text"],["expected_exit_price","Expected exit price ($)","250000","number"],["buyer_repairs","Buyer repair estimate ($)","30000","number"],["buyer_funding_holding","Buyer funding / holding ($)","8000","number"],["buyer_closing","Buyer closing costs ($)","5000","number"],["buyer_selling_costs","Buyer selling costs ($)","10000","number"],["buyer_minimum_profit","Buyer minimum profit ($)","40000","number"],["target_assignment_fee","Target assignment fee ($)","20000","number"],["owner_transaction_costs","Your transaction costs ($)","4000","number"],["partner_payout_allowance","Partner payout allowance ($)","0","number"],["contingency","Contingency ($)","5000","number"],["desired_owner_net","Desired net to you ($)","10000","number"],["basis","Assumptions / evidence basis","Enter sources and what remains unverified.","text"]];
  fields.forEach(([name,label,placeholder,type])=>{const w=node("label",label);const i=type==="text"&&name==="basis"?node("textarea"):node("input");i.name=name;i.required=true;i.placeholder=placeholder;if(type==="number"){i.type="number";i.min="0";i.step="0.01";i.value=placeholder;}w.append(i);underwriting.append(w);});
  const uwButton=node("button","Save underwriting scenarios","button primary");uwButton.type="submit";underwriting.append(uwButton);
  underwriting.addEventListener("submit",e=>{e.preventDefault();const v=values(underwriting);fields.filter(f=>f[3]==="number").forEach(f=>v[f[0]]=Number(v[f[0]]));runForm(underwriting,()=>api("/api/deals/"+deal.id+"/underwriting",v),"Underwriting scenarios saved.");});
  box.append(node("h3","Manual underwriting"),underwriting);
  if(deal.underwriting){const result=deal.underwriting.result;box.append(node("p",result.warning,"note"));const grid=node("div",undefined,"scenario-grid");Object.entries(result.scenarios).forEach(([name,s])=>{const card=node("article",undefined,"scenario-card");card.append(node("h3",name.toUpperCase()));card.append(node("p","Owner max contract: "+amount("money",s.owner_max_contract_price)));card.append(node("p","Planned owner net: "+amount("money",s.planned_owner_net)));if(s.buyer_acquisition_ceiling!==null)card.append(node("p","Buyer ceiling: "+amount("money",s.buyer_acquisition_ceiling)));card.append(node("span",s.profitable?"Meets entered target":"Below entered target","pill "+(s.profitable?"":"open")));grid.append(card);});box.append(grid);}
  const match=node("button","Run buyer matching","button");match.type="button";match.addEventListener("click",()=>runForm(match.form||box,()=>api("/api/deals/"+deal.id+"/buyer-matches",{}),"Buyer matching complete."));
  box.append(match);
  const matches=deal.buyer_matches?.matches||[];
  const matchBox=node("div");matches.forEach(m=>{const card=node("article",undefined,"evidence-row");card.append(node("strong",m.name+(m.company?" · "+m.company:"")));card.append(node("p",m.eligible_on_recorded_criteria?"Recorded criteria fit":"Criteria gaps: "+m.reasons.filter(r=>r!=="funding evidence requires current owner verification").join(", "),"small"));card.append(node("p",m.funding_verified_currently?"Funding evidence marked current by owner":"Funding evidence needs current owner review","muted small"));matchBox.append(card);});box.append(matchBox);
}
function renderBuyers() {
 const box=$("buyer-results"); box.replaceChildren();
 if(!state.buyers.length){box.append(node("p","No buyers saved yet.","muted small"));return;}
 state.buyers.forEach(b=>{const row=node("article",undefined,"evidence-row");row.append(node("strong",b.name+(b.company?" · "+b.company:"")));row.append(node("p",b.locations.join(" · ")+" | "+b.strategies.join(", ")+" | max "+amount("money",b.max_total_price),"small"));row.append(node("p","Funding status: "+b.funding_status+" (owner-entered)","muted small"));box.append(row);});
}
function bindDealForms(){
 $("deal-form").addEventListener("submit",e=>{e.preventDefault();const f=e.currentTarget;const v=values(f);v.property_id=selected;runForm(f,async()=>{selectedDeal=(await api("/api/deals",v)).id;},"Deal started.",true);});
 $("buyer-form").addEventListener("submit",e=>{e.preventDefault();const f=e.currentTarget;const v=values(f);v.locations=v.locations.split("\n").map(x=>x.trim()).filter(Boolean);v.strategies=v.strategies==="both"?["assignment","resale"]:[v.strategies];v.property_types=v.property_types.split(",").map(x=>x.trim()).filter(Boolean);v.max_total_price=Number(v.max_total_price);v.max_repairs=Number(v.max_repairs);if(v.funding_status!=="verified"){v.verified_at="";v.verification_reference="";}else if(v.verified_at){v.verified_at=new Date(v.verified_at+"T00:00:00Z").toISOString();}runForm(f,()=>api("/api/buyers",v),"Buyer saved.",true);});
}

function render() {
  $("count-properties").textContent = state.properties.length;
  $("count-facts").textContent = state.facts.length;
  $("count-open").textContent = state.predictions.filter(p => !p.resolved_at).length;
  $("count-outcomes").textContent = state.observations.length;
  renderList();
  const property = state.properties.find(p => p.id === selected);
  $("empty").hidden = Boolean(property);
  $("detail").hidden = !property;
  if (!property) return;
  $("property-title").textContent = property.address;
  $("property-location").textContent = property.city + ", " + property.state + (property.zip ? " " + property.zip : "");
  renderFacts(); renderPredictions(); renderLearning(); renderDealBoard(); renderBuyers();
}
async function refresh() {
  state = await api("/api/state");
  if (!state.properties.some(p => p.id === selected)) selected = state.properties[0]?.id || null;
  $("connection").textContent = "Saved locally";
  render();
}
async function runForm(form, action, success, reset = false) {
  const buttons = Array.from(document.querySelectorAll("button[type=submit]"));
  buttons.forEach(button => button.disabled = true);
  try {
    await action();
    if (reset) form.reset();
    try { await refresh(); message(success); }
    catch (error) { message("Saved, but the view could not refresh. Reload the page to see the update.", true); }
  } catch (error) { message(error.message, true); }
  finally { buttons.forEach(button => button.disabled = false); }
}
$("property-form").addEventListener("submit", event => {
  event.preventDefault();
  const form = event.currentTarget;
  runForm(form, async () => {
    const property = await api("/api/properties", values(form));
    selected = property.id;
  }, "Property saved.", true);
});
$("fact-form").addEventListener("submit", event => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = values(form);
  data.property_id = selected;
  data.confidence = Number(data.confidence);
  if (data.value_format === "number") {
    if (!data.value.trim() || !Number.isFinite(Number(data.value))) {
      message("Enter a valid numeric value, or select Text.", true); return;
    }
    data.value = Number(data.value);
  } else if (data.value_format === "boolean") {
    if (!["true", "false"].includes(data.value.trim().toLowerCase())) {
      message("Enter true or false.", true); return;
    }
    data.value = data.value.trim().toLowerCase() === "true";
  }
  delete data.value_format;
  runForm(form, () => api("/api/facts", data), "Sourced fact recorded.", true);
});
$("prediction-form").addEventListener("submit", event => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = values(form);
  data.property_id = selected;
  data.predicted_value = Number(data.predicted_value);
  data.confidence = Number(data.confidence);
  runForm(form, () => api("/api/predictions", data), "Estimate saved.", true);
});
$("search").addEventListener("input", () => { if (state) renderList(); });
bindDealForms();
refresh().catch(error => {
  $("connection").textContent = "Offline";
  message("Could not connect to the app: " + error.message + ". Reload after starting the server.", true);
});
