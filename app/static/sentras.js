'use strict';
const $ = id => document.getElementById(id);
async function api(path, data) {
  const response = await fetch(path, data ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)} : {});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
function show(container, value) { container.textContent = JSON.stringify(value, null, 2); }
async function reload() {
  const [state, meta, temporal] = await Promise.all([api('/api/sentras/evidence'), api('/api/sentras/meta?limit=100'), api('/api/sentras/temporal')]);
  $('readiness').textContent = `Data.gov: ${meta.provider_readiness.data_gov}. Active sources: ${meta.summary.active}. Awaiting review: ${meta.summary.quarantined + meta.summary.proposed}.`;
  $('sources').replaceChildren();
  $('profile-source').replaceChildren();
  $('temporal-source').replaceChildren();
  for (const source of meta.active_registry) {
    const option = document.createElement('option'); option.value = source.id; option.textContent = `${source.name} · ${source.id}`; $('profile-source').append(option);
    $('temporal-source').append(option.cloneNode(true));
  }
  for (const profile of state.profiles) {
    const p = document.createElement('p'); p.textContent = `${profile.sentra_id} · ${Object.keys(profile.field_map).join(', ')} · ${profile.cost_cents} cents per call`; $('sources').append(p);
  }
  if (!state.profiles.length) $('sources').textContent = 'No reviewed evidence mappings yet.';
  $('runs').replaceChildren();
  for (const run of state.runs) {
    const details = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
    summary.textContent = `${run.request.subject} · ${run.status} · ${new Date(run.started_at * 1000).toLocaleString()}`;
    show(pre, run.result); details.append(summary, pre); $('runs').append(details);
  }
  if (!state.runs.length) $('runs').textContent = 'No evidence runs yet.';
  $('temporal-sources').replaceChildren();
  for (const source of temporal.sources) {
    const p = document.createElement('p'); p.textContent = `${source.sentra_id} · ${source.observations} observations · ${source.changes} changes · interval ${source.interval_seconds / 3600} hours${source.last_error ? ' · ' + source.last_error : ''}`; $('temporal-sources').append(p);
  }
  if (!temporal.sources.length) $('temporal-sources').textContent = 'No source observations yet.';
  $('temporal-events').replaceChildren();
  for (const event of temporal.events.slice(0, 15)) {
    const details = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
    summary.textContent = `${event.kind} · ${new Date(event.observed_at * 1000).toLocaleString()}`;
    show(pre, event.details); details.append(summary, pre); $('temporal-events').append(details);
  }
}
let busy = false;
let pendingKey = null;
let pendingBody = null;
async function research(collect) {
  if (busy || !$('research').reportValidity()) return;
  busy = true; $('collect').disabled = true; $('status').textContent = collect ? 'Collecting evidence…' : 'Planning…';
  const form = Object.fromEntries(new FormData($('research')));
  const data = {...form, capabilities: form.capabilities.split(',').map(x => x.trim()).filter(Boolean)};
  for (const k of ['threshold', 'max_cost_cents', 'max_calls', 'max_age_hours']) data[k] = Number(form[k]);
  if (collect) {
    const body = JSON.stringify(data);
    if (!pendingKey || pendingBody !== body) {pendingKey = crypto.randomUUID(); pendingBody = body;}
    data.request_key = pendingKey;
  }
  try {
    const result = await api(`/api/sentras/evidence/${collect ? 'run' : 'plan'}`, data);
    show($('plan'), result); $('status').textContent = collect ? `Evidence run: ${result.status}` : 'Plan ready.';
    if (collect && result.status !== 'running') {pendingKey = null; pendingBody = null;}
    await reload();
  } catch (error) { $('status').textContent = error.message; }
  finally {busy = false; $('collect').disabled = false;}
}
$('research').addEventListener('submit', event => {event.preventDefault(); research(false);});
$('collect').addEventListener('click', () => research(true));
$('profile').addEventListener('submit', async event => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.target));
  const field_map = Object.create(null);
  try {
    for (const line of values.field_map.split('\n').filter(x => x.trim())) {
      const separator = line.indexOf(':'); if (separator < 1) throw new Error('Use capability: field, field on each mapping line.');
      const cap = line.slice(0, separator).trim();
      if (Object.hasOwn(field_map, cap)) throw new Error('Each capability must appear only once.');
      field_map[cap] = line.slice(separator + 1).split(',').map(x => x.trim()).filter(Boolean);
    }
    await api('/api/sentras/evidence/profile', {...values, field_map, requires: values.requires.split(',').map(x => x.trim()).filter(Boolean), confidence: Number(values.confidence), cost_cents: Number(values.cost_cents), owner_reviewed: values.owner_reviewed === 'on'});
    $('status').textContent = 'Reviewed source mapping saved.'; await reload();
  } catch (error) {$('status').textContent = error.message;}
});
$('temporal-policy').addEventListener('submit', async event => {
  event.preventDefault(); const values = Object.fromEntries(new FormData(event.target));
  try {
    await api('/api/sentras/temporal/policy', {sentra_id: values.sentra_id, base_interval_seconds: Number(values.base_hours) * 3600, min_interval_seconds: Number(values.min_hours) * 3600, daily_call_limit: Number(values.daily_call_limit), daily_cost_limit_cents: Number(values.daily_cost_limit_cents), cost_cents: Number(values.cost_cents), note: values.note, enabled: values.enabled === 'on', owner_reviewed: values.owner_reviewed === 'on'});
    $('status').textContent = 'Adaptive polling policy saved.'; await reload();
  } catch (error) {$('status').textContent = error.message;}
});
reload().catch(error => {$('status').textContent = error.message;});
