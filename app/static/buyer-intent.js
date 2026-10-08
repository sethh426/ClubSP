const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stamp = value => value ? new Date(value).toLocaleString() : 'Not checked yet';
const postEvidence = s => s.title ? `<p class="small">Publisher post: ${esc(s.title)}</p>${s.discovered_via ? `<p class="small">Discovered automatically from <a href="${esc(s.discovered_via)}" target="_blank" rel="noopener noreferrer">publisher feed</a>. This supports a company claim, not a confirmed buyer request.</p>` : ''}` : '';
let state;
let busy = false;
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Buyer research is unavailable.');
  return result;
}
function render() {
  $('counts').textContent = `${state.summary.total} saved findings · ${state.summary.buying_requests} open buying requests · ${state.summary.shortlisted} shortlisted`;
  const next = state.signals.find(s => s.status !== 'dismissed' && ['buying_request','company_claim'].includes(s.category));
  $('next-step').textContent = next ? `Start with ${next.name}. ${next.next_action}` : 'Automatic checks will collect company claims. Add any buying posts you find, then review the evidence before creating a prospect.';
  $('coverage').textContent = state.coverage;
  const run = state.post_runs?.[0];
  $('post-discovery').textContent = !run ? 'Publisher discovery starts automatically when its feed is due.' : run.status === 'failed' ? 'The last publisher discovery failed. Existing findings are retained; check the source status below.' : `Last publisher discovery: ${run.scanned} posts scanned · ${run.imported} buying-claim posts retained · ${run.skipped} posts skipped. Retained posts are supporting company evidence, not confirmed buyer requests.`;
  $('monitor-status').textContent = state.enabled ? 'Daily public checks are on. Findings are saved here without using property-provider requests.' : 'Public checks are paused. Your saved evidence remains available.';
  $('toggle').textContent = state.enabled ? 'Pause checks' : 'Resume checks';
  $('refresh').disabled = !state.enabled || busy;
  $('sources').innerHTML = state.sources.map(s => `<div class="source"><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.name)}${s.type === 'publisher_feed' ? ' · Public post feed' : ' · Company page'}</a><p class="small">Last successful check: ${esc(stamp(s.checked_at))}<br>Next eligible check: ${esc(stamp(s.next_check))}</p>${s.error ? `<p class="warning">${esc(s.error)}</p>` : ''}</div>`).join('');
  const filter = $('filter').value;
  const signals = state.signals.filter(s => filter === 'all' || (filter === 'active' ? s.status !== 'dismissed' : ['dismissed','shortlisted'].includes(filter) ? s.status === filter : s.category === filter));
  $('signals').innerHTML = signals.map(s => `<article class="signal" data-id="${esc(s.id)}"><span class="badge">${esc(s.label)}</span><span class="badge">${esc(s.status)}</span><h3>${esc(s.name)}</h3><p class="small">${esc(s.freshness)}${s.published_on ? ` (${esc(s.published_on)})` : ''} · Last observed: ${esc(stamp(s.last_seen))}${s.recent_check ? '' : ' · Needs a fresh source check'}</p><blockquote>${esc(s.text)}</blockquote><p>Markets mentioned: ${esc(s.markets.join(', ') || 'Not established')}</p><p><strong>Next:</strong> ${esc(s.next_action)}</p><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">Read original source</a><details><summary>Evidence history (${s.history.length} most recent versions)</summary>${s.history.map(h=>`<p class="small">Observed ${esc(stamp(h.observed_at))} · ${esc(h.published_on || 'Publication date unknown')}</p><blockquote>${esc(h.text)}</blockquote>`).join('')}</details><div class="actions">${s.relationship_id ? `<a class="button primary" href="/relationships#relationship-${esc(s.relationship_id)}">Continue buyer qualification</a>` : s.status !== 'dismissed' && ['buying_request','company_claim'].includes(s.category) ? '<button class="button primary" data-action="relationship">Prepare buyer prospect</button>' : ''}<button class="button" data-action="review" data-value="${s.status === 'shortlisted' ? 'new' : 'shortlisted'}">${s.status === 'shortlisted' ? 'Remove from shortlist' : 'Shortlist'}</button><button class="button" data-action="review" data-value="${s.status === 'dismissed' ? 'new' : 'dismissed'}">${s.status === 'dismissed' ? 'Restore' : 'Dismiss'}</button></div><div class="prospect-result" aria-live="polite"></div></article>`).join('') || '<p>No findings in this view.</p>';
  signals.forEach(s => { const card = document.querySelector(`[data-id="${s.id}"]`); if(card && s.title) card.querySelector('h3').insertAdjacentHTML('afterend', postEvidence(s)); });
}
async function load() { state = await api('/api/buyer-intent'); render(); }
async function action(work) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  $('status').textContent = 'Working…';
  try { const result = await work(); await load(); if(result?.afterRender) result.afterRender(); $('status').textContent = result?.message || result || 'Saved.'; }
  catch(error) { $('status').textContent = error.message; }
  finally { busy = false; document.querySelectorAll('button').forEach(b => b.disabled = false); $('refresh').disabled = !state?.enabled; }
}
$('filter').addEventListener('change', () => state && render());
$('capture').addEventListener('submit', event => {
  event.preventDefault();
  action(async () => { const data = Object.fromEntries(new FormData(event.target)); await api('/api/buyer-intent', data); event.target.reset(); $('filter').value = 'active'; return 'Finding saved. Review its evidence and next step below.'; });
});
$('refresh').addEventListener('click', () => action(async () => {
  const result = await api('/api/buyer-intent/refresh', {});
  if(result.status === 'already_running') return 'A source check is already running. Results will appear when it finishes.';
  const checked = result.sources?.filter(s=>s.status==='checked').length || 0;
  const failed = result.sources?.filter(s=>s.status==='failed').length || 0;
  return `${checked} sources checked; ${failed} could not be checked. Sources already attempted today wait until their next eligible check.`;
}));
$('toggle').addEventListener('click', () => action(async () => { await api('/api/buyer-intent/settings', {enabled:!state.enabled}); return state.enabled ? 'Checks paused.' : 'Checks resumed. The background worker checks due sources within a minute.'; }));
$('signals').addEventListener('click', event => {
  const button = event.target.closest('button[data-action]');
  if(!button) return;
  const card = button.closest('[data-id]');
  const id = card.dataset.id;
  action(async () => {
    const result = await api(`/api/buyer-intent/${id}/${button.dataset.action}`, button.dataset.action === 'review' ? {status:button.dataset.value} : {});
    if(result.relationship_id) { location.href = `/relationships#relationship-${result.relationship_id}`; return 'Prospect prepared. Buying criteria and contact permission still need review.'; }
    if(result.existing_relationships) {
      // Keep possible identity matches visible without silently overwriting their records.
      return {message: 'A possible existing contact was found. Use its review link below.', afterRender: () => {
      const current = document.querySelector(`[data-id="${id}"] .prospect-result`);
      current.innerHTML = '<p>A saved contact has the same name. Review it before creating a duplicate.</p>' + result.existing_relationships.map(r=>`<a class="button" href="/relationships#relationship-${esc(r)}">Review saved contact</a>`).join('');
      }};
    }
    return 'Research review saved.';
  });
});
load().catch(error => { $('status').textContent = error.message; });
setInterval(() => { if(!busy && document.visibilityState === 'visible') load().catch(()=>{}); }, 60000);
