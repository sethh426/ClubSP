"use strict";

const fundingMoney = value => value === null ? "Unknown" : new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"}).format(value);
function fundingNode(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function fundingKey() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map(b => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
async function fundingApi(path, data) {
  const options = data === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)};
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Funding request failed");
  return body;
}
function fundingMessage(text, error = false) {
  const message = document.getElementById("funding-message");
  message.textContent = text;
  message.className = "message" + (error ? " error" : "");
  message.hidden = false;
}
function fundingFilter() {
  const value = document.getElementById("funding-filter").value;
  let visible = 0;
  document.querySelectorAll(".funding-deal").forEach(card => {
    card.hidden = !(value === "all" || (value === "active" ? card.dataset.actionStatus !== "historical" : card.dataset.actionStatus === value));
    if (!card.hidden) visible++;
  });
  document.getElementById("funding-empty").hidden = visible > 0;
}
function fundingField(form, name, label, value = "", type = "text", required = true) {
  const wrap = fundingNode("label", label);
  const input = fundingNode(type === "textarea" ? "textarea" : "input");
  input.name = name;
  if (type !== "textarea") input.type = type;
  input.value = value;
  input.required = required;
  input.maxLength = type === "textarea" ? 2000 : 500;
  if (type === "number") { input.min = "0"; input.step = "0.01"; }
  wrap.append(input); form.append(wrap);
}
function fundingSelect(form, name, label, options, value) {
  const wrap = fundingNode("label", label), select = fundingNode("select"); select.name = name;
  options.forEach(([key, text]) => {const option = fundingNode("option", text); option.value = key; select.append(option);});
  select.value = value; wrap.append(select); form.append(wrap);
}
function fundingForm(deal, card) {
  const prior = deal.review || {};
  const details = fundingNode("details", undefined, "workspace-details");
  details.append(fundingNode("summary", deal.review ? "Add a new funding review version" : "Record partner or lender funding"));
  const form = fundingNode("form", undefined, "workspace-form funding-form");
  fundingSelect(form, "kind", "Funding source", [["lender","Lender"],["partner","Partner"]], prior.kind || "lender");
  fundingField(form, "counterparty", "Partner or lender name", prior.counterparty || "");
  fundingSelect(form, "status", "Recorded status", [["pending","Pending review"],["owner_reviewed","Owner reviewed evidence"],["withdrawn","Withdrawn"]], "pending");
  for (const [name, label] of [["required_funding","External funding needed ($)"],["committed_funding","Amount earmarked for this deal ($)"],["owner_cash_required","Your cash required ($)"],["contingent_liability","Additional owner liability / guarantee ($)"],["known_financing_cost","Known financing / partner costs ($)"]]) {
    fundingField(form, name, label, prior[name] ?? "", "number");
  }
  fundingField(form, "reviewed_on", "Evidence review date", "", "date");
  fundingField(form, "expires_on", "Commitment / terms expire on", prior.expires_on || "", "date");
  for (const [name, label] of [["funding_need_reference","Funding requirement basis reference"],["terms_reference","Commitment / terms evidence reference"],["allocation_reference","Deal-specific funding allocation reference"],["obligations_reference","Recourse / guarantees / repayment review reference"],["costs_reference","Costs reconciled with underwriting reference"],["conditions_reference","All conditions resolved / none: evidence reference"]]) {
    fundingField(form, name, label, prior[name] || "", "text", name === "funding_need_reference");
  }
  fundingField(form, "unresolved_conditions", "Unresolved conditions (one per line; leave blank only after review)", (prior.unresolved_conditions || []).join("\n"), "textarea", false);
  fundingField(form, "note", "Review notes and limits", "", "textarea");
  const label = fundingNode("label", undefined, "check-label"), check = fundingNode("input");
  check.type = "checkbox"; check.name = "owner_confirmed";
  label.append(check, document.createTextNode("I reviewed the deal-specific allocation, obligations, costs, and remaining conditions.")); form.append(label);
  const button = fundingNode("button", "Save funding review", "button primary"); button.type = "submit"; form.append(button);
  let key = fundingKey(), attemptedPayload = null;
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    data.owner_confirmed = check.checked;
    data.unresolved_conditions = data.unresolved_conditions.split("\n").map(s => s.trim()).filter(Boolean);
    data.supersedes_id = deal.review?.id || "";
    data.financial_plan_id = deal.current_financial_plan_id;
    data.underwriting_id = deal.current_underwriting_id;
    const signature = JSON.stringify(data);
    if (attemptedPayload !== null && attemptedPayload !== signature) key = fundingKey();
    attemptedPayload = signature; data.request_key = key;
    button.disabled = true;
    let saved = false;
    try {
      await fundingApi(`/api/deals/${deal.id}/funding`, data); saved = true;
      fundingMessage("Funding review saved. No loan, payment, or commitment was executed.");
      await fundingLoad();
    } catch (error) {
      fundingMessage(saved ? "Review saved, but the view could not refresh. Reload before adding another version." : error.message, true);
    } finally { if (!saved) button.disabled = false; }
  });
  details.append(form); card.append(details);
}
async function fundingLoad() {
  const state = await fundingApi("/api/funding");
  document.getElementById("pipeline-context").textContent = state.pipeline.opportunity_integration_available
    ? "Combines current opportunity reviews, funding checks, and open tasks. Ordered by owner review status and recorded downside contribution."
    : "Combines funding checks and open tasks. Current opportunity reviews are unavailable; review evidence and buyer fit in the property workspace.";
  const shortlist = document.getElementById("funding-shortlist"); shortlist.replaceChildren();
  state.pipeline.shortlist.forEach(item => {
    const li = fundingNode("li"), link = fundingNode("a", item.address); link.href = `#funding-${item.deal_id}`;
    link.addEventListener("click", () => {document.getElementById("funding-filter").value = "active"; fundingFilter();});
    li.append(link, fundingNode("p", item.next_action, "small")); shortlist.append(li);
  });
  const summary = document.getElementById("funding-summary"); summary.replaceChildren();
  for (const [name, label] of [["active_deals","Active deals"],["checks_pass","Recorded checks pass"],["needs_review","Need funding review"]]) {
    const metric = fundingNode("article"); metric.append(fundingNode("span", label), fundingNode("strong", String(state.summary[name]))); summary.append(metric);
  }
  const list = document.getElementById("funding-deals"); list.replaceChildren();
  if (!state.deals.length) {list.append(fundingNode("p", "Add a property and deal in the property workspace, then record underwriting and proposed terms.")); return;}
  state.deals.forEach(deal => {
    const card = fundingNode("article", undefined, "panel funding-deal"); card.dataset.dealId = deal.id;
    card.id = `funding-${deal.id}`; card.dataset.actionStatus = deal.action_status;
    card.append(fundingNode("h2", deal.address), fundingNode("p", `${deal.city} · ${deal.strategy} · ${deal.stage}`, "muted small"));
    card.append(fundingNode("p", deal.checks_pass ? "Recorded funding checks pass" : "Funding review needed", "finance-risk" + (deal.checks_pass ? "" : " funding-needs-review")));
    card.append(fundingNode("p", `Forecast contribution: ${fundingMoney(deal.forecast_net)} · Your cash limit: ${fundingMoney(deal.owner_cash_limit)} · External shortfall: ${fundingMoney(deal.funding_gap)}`, "small"));
    card.append(fundingNode("p", "Next step: " + deal.next_action));
    card.append(fundingNode("h3", "Deal action plan"), fundingNode("p", deal.pipeline_next_action));
    card.append(fundingNode("p", `Downside contribution: ${fundingMoney(deal.downside_net)} · Unrecovered cash: ${fundingMoney(deal.unrecovered_cash)} · Reconciled net: ${fundingMoney(deal.reconciled_net)}`, "small"));
    card.append(fundingNode("p", deal.opportunity_checks_available
      ? `Opportunity review: ${deal.opportunity_decision.replaceAll("_", " ")} · Current criteria-fit buyers with recorded funding review: ${deal.current_criteria_fit_buyers}`
      : "Current opportunity / buyer checks: unavailable for this deal", "muted small"));
    const actions = fundingNode("details", undefined, "workspace-details");
    actions.append(fundingNode("summary", `Action checklist · ${deal.action_items.length}`));
    const actionList = fundingNode("ul"); deal.action_items.forEach(item => actionList.append(fundingNode("li", `${item.category}: ${item.text}`))); actions.append(actionList); card.append(actions);
    const tasks = fundingNode("details", undefined, "workspace-details"); tasks.append(fundingNode("summary", `Open tasks · ${deal.open_tasks.length}`));
    deal.open_tasks.forEach(task => tasks.append(fundingNode("p", `${task.title} · ${task.owner} · Due ${task.due_on || "unscheduled"}${task.overdue ? " · Overdue" : ""}`, "small"))); card.append(tasks);
    if (deal.blockers.length) {const reasons = fundingNode("ul", undefined, "blocker-list"); deal.blockers.forEach(reason => reasons.append(fundingNode("li", reason))); card.append(reasons);}
    if (deal.review) {
      const r = deal.review;
      card.append(fundingNode("p", `${r.counterparty} · ${r.status} · Reviewed ${r.reviewed_on} · Expires ${r.expires_on}`, "muted small"));
      card.append(fundingNode("p", `Needed ${fundingMoney(r.required_funding)} · Earmarked ${fundingMoney(r.committed_funding)} · Your cash ${fundingMoney(r.owner_cash_required)} · Additional liability ${fundingMoney(r.contingent_liability)} · Known financing / partner costs ${fundingMoney(r.known_financing_cost)}`, "small"));
    }
    if (deal.can_record) fundingForm(deal, card);
    const history = fundingNode("details", undefined, "workspace-details"); history.append(fundingNode("summary", `Funding history · ${deal.history.length} ${deal.history.length === 1 ? "version" : "versions"}`));
    deal.history.forEach(r => {
      const entry = fundingNode("section", undefined, "evidence-row");
      entry.append(fundingNode("strong", `${r.counterparty} · ${r.status} · ${r.reviewed_on}`), fundingNode("p", r.note));
      for (const name of ["funding_need_reference","terms_reference","allocation_reference","obligations_reference","costs_reference","conditions_reference"]) {
        if (r[name]) entry.append(fundingNode("p", `${name.replaceAll("_", " ")}: ${r[name]}`));
      }
      entry.append(fundingNode("p", "Recorded version: " + r.id, "muted small")); history.append(entry);
    }); card.append(history); list.append(card);
  });
  fundingFilter();
}
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("funding-filter").addEventListener("change", fundingFilter);
  fundingLoad().catch(error => fundingMessage(error.message, true));
});
