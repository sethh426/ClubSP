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
  const [state, meta, temporal, shadow] = await Promise.all([api('/api/sentras/evidence'), api('/api/sentras/meta?limit=100'), api('/api/sentras/temporal'), api('/api/sentras/shadow')]);
  $('readiness').textContent = `Data.gov: ${meta.provider_readiness.data_gov}. Active sources: ${meta.summary.active}. Awaiting review: ${meta.summary.quarantined + meta.summary.proposed}.`;
  $('sources').replaceChildren();
  $('profile-source').replaceChildren();
  $('temporal-source').replaceChildren();
  $('shadow-baseline').replaceChildren();
  $('shadow-challenger').replaceChildren();
  for (const source of meta.active_registry) {
    const option = document.createElement('option'); option.value = source.id; option.textContent = `${source.name} · ${source.id}`; $('profile-source').append(option);
    $('temporal-source').append(option.cloneNode(true));
  }
  for (const profile of state.profiles) {
    const p = document.createElement('p'); p.textContent = `${profile.sentra_id} · ${Object.keys(profile.field_map).join(', ')} · ${profile.cost_cents} cents per call`; $('sources').append(p);
    if (meta.active_registry.some(s => s.id === profile.sentra_id && s.activated_at === profile.activation)) {
      const option = document.createElement('option'); option.value = profile.sentra_id; option.textContent = profile.sentra_id; $('shadow-baseline').append(option);
    }
  }
  renderCandidates(meta.candidates);
  for (const candidate of meta.candidates.filter(c => ['schema_probed', 'proposed', 'approved', 'active'].includes(c.state) && c.discovery_provider !== 'apify_store')) {
    const option = document.createElement('option'); option.value = candidate.fingerprint; option.textContent = candidate.name; $('shadow-challenger').append(option);
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
  renderShadow(shadow);
  await renderWatches();
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

function propertyCard(result,container) {
  container.replaceChildren();
  const heading=document.createElement('h3'); heading.textContent=result.address || 'Property research';
  const message=document.createElement('p'); message.textContent=result.message;
  container.append(heading,message);
  if (result.assessed_value !== null && result.assessed_value !== undefined) {
    const value=document.createElement('p'); value.textContent=`County assessed value: ${new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(result.assessed_value)}`; container.append(value);
  }
  const note=document.createElement('p'); note.className='muted small'; note.textContent=result.note || '';container.append(note);
  for(const source of result.sources || []) {
    const link=document.createElement('a'); link.href=source.url;link.textContent='County source';link.target='_blank';link.rel='noopener noreferrer';
    const date=document.createElement('p');date.textContent=`Retrieved ${new Date(source.retrieved_at*1000).toLocaleString()}`;container.append(link,date);
  }
}
async function renderWatches() {
  const state=await api('/api/sentras/property/watches');$('property-watches').replaceChildren();
  for(const watch of state.watches.filter(w=>w.enabled)) {
    const card=document.createElement('section');card.className='property-card';propertyCard({...watch.result,address:watch.address},card);
    const next=document.createElement('p');next.textContent=`Next update: ${new Date(watch.next_due*1000).toLocaleString()}`;
    const stop=document.createElement('button');stop.className='button';stop.textContent='Stop daily updates';
    stop.addEventListener('click',async()=>{stop.disabled=true;try{await api('/api/sentras/property/stop',{id:watch.id});await renderWatches();}catch(e){$('status').textContent=e.message;stop.disabled=false;}});
    card.append(next,stop);$('property-watches').append(card);
  }
  if(!$('property-watches').children.length)$('property-watches').textContent='Research a property to start its daily updates.';
}
$('address-search').addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;busy=true;
  const button=event.target.querySelector('button');button.disabled=true;$('status').textContent='Searching county addresses…';$('address-matches').replaceChildren();
  try {
    const result=await api('/api/sentras/property/search',Object.fromEntries(new FormData(event.target)));
    $('address-coverage').textContent=result.coverage_note;
    for(const match of result.matches) {
      const card=document.createElement('section');card.className='property-card';
      const name=document.createElement('h3');name.textContent=match.address;
      const research=document.createElement('button');research.className='button primary';research.textContent='Research property';let requestKey=null;
      research.addEventListener('click',async()=>{
        if(busy)return;busy=true;research.disabled=true;$('status').textContent='Researching this property…';
        if(!requestKey)requestKey=crypto.randomUUID();
        try{const summary=await api('/api/sentras/property/research',{subject:match.subject,request_key:requestKey,watch:$('watch-daily').checked});propertyCard(summary,$('property-result'));$('status').textContent=summary.message;if(summary.status!=='running')requestKey=null;await renderWatches();}
        catch(e){$('status').textContent=e.message;}finally{busy=false;research.disabled=false;}
      });card.append(name,research);$('address-matches').append(card);
    }
    $('status').textContent=result.matches.length ? 'Choose the matching property below.' : 'No match in the connected records. Try just the house number and street name; this source does not cover every county property.';
    if(result.limited)$('status').textContent+=' Showing up to 25 records; narrow the address if needed.';
  }catch(e){$('status').textContent=e.message;}finally{busy=false;button.disabled=false;}
});

function parseMapping(text) {
  const mapping = Object.create(null);
  for (const line of text.split('\n').filter(x => x.trim())) {
    const separator = line.indexOf(':');
    if (separator < 1) throw new Error('Use capability: field, field on each mapping line.');
    const cap = line.slice(0, separator).trim();
    if (Object.hasOwn(mapping, cap)) throw new Error('Each capability must appear only once.');
    mapping[cap] = line.slice(separator + 1).split(',').map(x => x.trim()).filter(Boolean);
  }
  return mapping;
}
function renderShadow(state) {
  $('shadow-reports').replaceChildren(); $('shadow-trials').replaceChildren();
  $('shadow-experiment-id').replaceChildren(); $('shadow-trial-id').replaceChildren();
  for (const experiment of state.experiments) {
    const option = document.createElement('option'); option.value = experiment.id; option.textContent = experiment.name; $('shadow-experiment-id').append(option);
    const report = state.reports.find(r => r.experiment_id === experiment.id);
    const details = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
    summary.textContent = `${experiment.name} · ${report.recommendation} · ${report.trial_count} trials`; show(pre, report); details.append(summary, pre); $('shadow-reports').append(details);
  }
  if (!state.experiments.length) $('shadow-reports').textContent = 'No shadow experiments yet.';
  for (const trial of state.trials) {
    const details = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
    summary.textContent = `${trial.status} · ${trial.id} · ${new Date(trial.started_at * 1000).toLocaleString()}`; show(pre, trial.result); details.append(summary, pre); $('shadow-trials').append(details);
    if (['completed', 'failed'].includes(trial.status)) {
      const option = document.createElement('option'); option.value = trial.id; option.textContent = `${trial.id} · ${trial.status}`; $('shadow-trial-id').append(option);
    }
  }
}
$('shadow-experiment').addEventListener('submit', async event => {
  event.preventDefault(); const v = Object.fromEntries(new FormData(event.target));
  try {
    const rules = [];
    if (v.rule.trim()) {
      const [field, op, ...pieces] = v.rule.split(',').map(s => s.trim());
      const value = pieces.join(','); rules.push({field, op, value: ['gte', 'lte'].includes(op) ? Number(value) : value});
    }
    await api('/api/sentras/shadow/experiment', {...v, rules, field_map: parseMapping(v.field_map), cost_cents: Number(v.cost_cents), daily_cost_limit_cents: Number(v.daily_cost_limit_cents), owner_reviewed: v.owner_reviewed === 'on'});
    $('status').textContent = 'Shadow experiment created.'; await reload();
  } catch (error) {$('status').textContent = error.message;}
});
let trialBusy = false, trialKey = null, trialBody = null;
$('shadow-trial').addEventListener('submit', async event => {
  event.preventDefault(); if (trialBusy) return;
  const v = Object.fromEntries(new FormData(event.target)), data = {...v, subjects: v.subjects.split('\n').map(s => s.trim()).filter(Boolean), max_cost_cents: Number(v.max_cost_cents)};
  const body = JSON.stringify(data); if (!trialKey || trialBody !== body) {trialKey = crypto.randomUUID(); trialBody = body;}
  trialBusy = true; const button = event.target.querySelector('button'); button.disabled = true;
  try {
    const result = await api('/api/sentras/shadow/trial', {...data, request_key: trialKey});
    if (result.status !== 'running') {trialKey = null; trialBody = null;}
    $('status').textContent = `Shadow trial: ${result.status}`; await reload();
  } catch (error) {$('status').textContent = error.message;}
  finally {trialBusy = false; button.disabled = false;}
});
$('shadow-review').addEventListener('submit', async event => {
  event.preventDefault(); const v = Object.fromEntries(new FormData(event.target)), outcomes = {};
  for (const arm of ['baseline', 'challenger']) {
    const values = {};
    for (const metric of ['false_hits', 'accepted_evidence', 'downstream_successes']) if (v[`${arm}_${metric}`] !== '') values[metric] = Number(v[`${arm}_${metric}`]);
    if (Object.keys(values).length) outcomes[arm] = values;
  }
  try {await api('/api/sentras/shadow/review', {...v, outcomes}); $('status').textContent = 'Outcome review saved.'; await reload();}
  catch (error) {$('status').textContent = error.message;}
});
function formField(form, name, labelText, value = '', multiline = false) {
  const label = document.createElement('label'); label.textContent = labelText;
  const input = document.createElement(multiline ? 'textarea' : 'input'); input.name = name; input.value = value; input.required = true; input.maxLength = multiline ? 4000 : 200; label.append(input); form.append(label); return input;
}
function renderCandidates(candidates) {
  $('source-candidates').replaceChildren();
  for (const source of candidates) {
    const details = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
    summary.textContent = `${source.name} · ${source.state}`;
    show(pre, {url: source.source_url, provider: source.discovery_provider, capabilities: source.capabilities, fields: source.probe.fields || [], metadata: source.metadata, assessment: source.assessment}); details.append(summary, pre);
    for (const [action, eligible] of [['probe', ['quarantined', 'metadata_probed', 'requarantined']], ['propose', ['schema_probed']]]) {
      if (!eligible.includes(source.state)) continue;
      const button = document.createElement('button'); button.className = 'button'; button.textContent = action === 'probe' ? 'Probe public source' : 'Propose for review'; button.type = 'button';
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {await api(`/api/sentras/meta/${action}`, {fingerprint: source.fingerprint}); $('status').textContent = `Source ${action} complete.`; await reload();}
        catch (error) {$('status').textContent = error.message; button.disabled = false;}
      }); details.append(button);
    }
    if (source.state === 'proposed') {
      const form = document.createElement('form'); formField(form, 'note', 'Source access, rights and usefulness review', '', true);
      const approve = document.createElement('button'); approve.className = 'button'; approve.textContent = 'Approve reviewed source'; approve.type = 'submit'; form.append(approve);
      form.addEventListener('submit', async event => {event.preventDefault(); approve.disabled = true; try {await api('/api/sentras/meta/review', {fingerprint: source.fingerprint, decision: 'approve', note: new FormData(form).get('note')}); $('status').textContent = 'Source approved; activation requires a separate action.'; await reload();} catch (error) {$('status').textContent = error.message; approve.disabled = false;}});
      details.append(form);
    }
    if (source.state === 'approved') {
      const form = document.createElement('form'); formField(form, 'sentra_id', 'New source identifier'); formField(form, 'family', 'Source family'); formField(form, 'jurisdiction', 'Verified county and state', source.jurisdiction_hint); formField(form, 'rights_note', 'Reviewed access and rights basis', '', true);
      const button = document.createElement('button'); button.type = 'submit'; button.className = 'button'; button.textContent = 'Activate approved source'; form.append(button);
      form.addEventListener('submit', async event => {event.preventDefault(); button.disabled = true; const v = Object.fromEntries(new FormData(form)); try {await api('/api/sentras/meta/activate', {...v, fingerprint: source.fingerprint, acquisition_mode: source.probe.shape === 'arcgis_layer' ? 'arcgis' : source.probe.shape === 'csv' ? 'file_parser' : 'direct_http'}); $('status').textContent = 'Source activated. Review its evidence mapping before collection.'; await reload();} catch (error) {$('status').textContent = error.message; button.disabled = false;}}); details.append(form);
    }
    $('source-candidates').append(details);
  }
  if (!candidates.length) $('source-candidates').textContent = 'No discovered source candidates yet.';
}
