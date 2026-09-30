"use strict";
const $ = (id) => document.getElementById(id);
let state = null;
let selected = null;

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
  renderFacts(); renderPredictions(); renderLearning();
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
refresh().catch(error => {
  $("connection").textContent = "Offline";
  message("Could not connect to the app: " + error.message + ". Reload after starting the server.", true);
});
