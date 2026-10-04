"use strict";

function commitmentDate(value) {
  if (!value) return "";
  return new Date(value + "T00:00:00Z").toISOString();
}

function commitmentFreshness(item, maxDays) {
  if (!item?.verified_at) return { current:false, ageDays:null };
  const verified = new Date(item.verified_at);
  if (Number.isNaN(verified.getTime())) return { current:false, ageDays:null };
  const ageDays = Math.max(0, (Date.now() - verified.getTime()) / 86400000);
  return { current: ageDays <= maxDays, ageDays: Math.floor(ageDays) };
}

function commitmentReconfirmControl(entityPath, item, maxDays) {
  const freshness = commitmentFreshness(item, maxDays);
  const d = workspaceDetails(freshness.current ? "Reconfirm evidence" : "Reconfirm stale evidence", false);
  const form = node("form", undefined, "workspace-form commitment-reconfirm-form");
  workspaceField(form, "verified_at", "Reconfirmed date", "date", new Date().toISOString().slice(0,10));
  workspaceField(form, "evidence_reference", "New confirmation / evidence reference");
  workspaceField(form, "note", "Reconfirmation notes", "textarea", "", false);
  workspaceSubmit(form, freshness.current ? "Record reconfirmation" : "Restore current status");
  form.addEventListener("submit", e => {
    e.preventDefault();
    const v = values(form);
    v.verified_at = commitmentDate(v.verified_at);
    runForm(form, () => api("/api/commitments/" + entityPath + "/" + item.id + "/reconfirm", v), "Commitment evidence reconfirmed.");
  });
  d.append(node("p",
    freshness.current
      ? "Current verification age: " + freshness.ageDays + " day(s)."
      : "Verification is outside ClubSP's " + maxDays + "-day freshness window and is excluded from current matching until reconfirmed.",
    "muted small"
  ), form);
  return d;
}

function commitmentStatusControl(entityPath, item, allowed) {
  const d = workspaceDetails("Change status", false);
  const form = node("form", undefined, "workspace-form commitment-status-form");
  workspaceSelect(form, "status", "New status", allowed.filter(x => x !== item.status).map(x => [x, readable(x)]));
  workspaceField(form, "note", "Reason / status note", "textarea");
  workspaceField(form, "evidence_reference", "Evidence reference");
  workspaceSubmit(form, "Save status");
  form.addEventListener("submit", e => {
    e.preventDefault();
    runForm(form, () => api("/api/commitments/" + entityPath + "/" + item.id + "/status", values(form)), "Commitment status updated.");
  });
  d.append(form);
  return d;
}

function renderCommitmentGraph() {
  const box = $("commitment-workspace");
  if (!box || !state?.commitment_graph) return;
  box.replaceChildren();
  const graph = state.commitment_graph;

  const summary = node("div", undefined, "metrics");
  const activeMandates = graph.buyer_mandates.filter(x => x.status === "active").length;
  const activeCapital = graph.capital_profiles.filter(x => x.status === "active").length;
  const ready = graph.deal_readiness.filter(x => x.score >= 60).length;
  [
    ["Standing mandates", activeMandates],
    ["Capital paths", activeCapital],
    ["Deals ≥60 readiness", ready],
    ["Recorded path outcomes", graph.recent_outcomes.length],
  ].forEach(([label, value]) => {
    const card = node("article"); card.append(node("span", label), node("strong", String(value))); summary.append(card);
  });
  box.append(summary);

  const mandateDetails = workspaceDetails("Add standing buyer mandate", false);
  const mf = node("form", undefined, "workspace-form commitment-mandate-form");
  const buyer = workspaceSelect(mf, "buyer_id", "Buyer", state.buyers.map(b => [b.id, b.name + (b.company ? " · " + b.company : "")]));
  workspaceField(mf, "name", "Mandate name", "text", "Standing buyer mandate", false);
  workspaceField(mf, "markets", "Markets (one city, state per line)", "textarea");
  workspaceSelect(mf, "strategies", "Strategies", [["assignment", "Assignment"], ["resale", "Resale"], ["both", "Both"]]);
  workspaceField(mf, "property_types", "Property types (comma separated; blank = any)", "text", "", false);
  workspaceField(mf, "max_total_price", "Maximum total price", "number");
  workspaceField(mf, "max_repairs", "Maximum repairs", "number", "0");
  workspaceField(mf, "max_active_reservations", "Maximum active deal reservations", "number", "1");
  workspaceField(mf, "target_units_per_month", "Target units per month", "number", "1");
  workspaceField(mf, "min_beds", "Minimum beds (optional)", "number", "", false);
  workspaceField(mf, "max_beds", "Maximum beds (optional)", "number", "", false);
  workspaceField(mf, "min_baths", "Minimum baths (optional)", "number", "", false);
  workspaceField(mf, "max_baths", "Maximum baths (optional)", "number", "", false);
  workspaceField(mf, "min_sqft", "Minimum square feet (optional)", "number", "", false);
  workspaceField(mf, "max_sqft", "Maximum square feet (optional)", "number", "", false);
  workspaceField(mf, "min_year_built", "Minimum year built (optional)", "number", "", false);
  workspaceField(mf, "max_year_built", "Maximum year built (optional)", "number", "", false);
  workspaceField(mf, "priority", "Priority 0–100", "number", "50");
  workspaceField(mf, "verified_at", "Verified date", "date");
  workspaceField(mf, "expires_at", "Expires date (optional)", "date", "", false);
  workspaceField(mf, "evidence_reference", "Evidence / confirmation reference");
  workspaceSubmit(mf, "Save standing mandate");
  if (!state.buyers.length) {
    buyer.disabled = true;
    mandateDetails.append(node("p", "Add a buyer first, then record a standing mandate for that buyer.", "muted small"));
  }
  mf.addEventListener("submit", e => {
    e.preventDefault();
    const v = values(mf);
    v.markets = v.markets.split("\n").map(x => x.trim()).filter(Boolean);
    v.strategies = v.strategies === "both" ? ["assignment", "resale"] : [v.strategies];
    v.property_types = v.property_types.split(",").map(x => x.trim()).filter(Boolean);
    v.max_total_price = Number(v.max_total_price);
    v.max_repairs = Number(v.max_repairs);
    v.max_active_reservations = Number(v.max_active_reservations);
    v.target_units_per_month = Number(v.target_units_per_month);
    ["min_beds","max_beds","min_baths","max_baths","min_sqft","max_sqft","min_year_built","max_year_built"].forEach(key => {
      if (v[key] === "") delete v[key]; else v[key] = Number(v[key]);
    });
    v.priority = Number(v.priority);
    v.verified_at = commitmentDate(v.verified_at);
    v.expires_at = commitmentDate(v.expires_at);
    v.status = "active";
    runForm(mf, () => api("/api/commitments/buyer-mandates", v), "Standing buyer mandate saved.", true);
  });
  mandateDetails.append(mf); box.append(mandateDetails);

  const capitalDetails = workspaceDetails("Add capital availability", false);
  const cf = node("form", undefined, "workspace-form commitment-capital-form");
  workspaceField(cf, "name", "Capital partner / source");
  workspaceSelect(cf, "provider_type", "Type", [["partner","Partner"],["lender","Lender"],["self","Self"],["other","Other"]]);
  workspaceField(cf, "markets", "Markets (one city, state per line; blank = any)", "textarea", "", false);
  workspaceSelect(cf, "strategies", "Strategies", [["assignment","Assignment"],["resale","Resale"],["both","Both"],["any","Any"]]);
  workspaceField(cf, "max_commitment", "Maximum commitment", "number");
  workspaceField(cf, "available_amount", "Currently available", "number");
  workspaceField(cf, "verified_at", "Verified date", "date");
  workspaceField(cf, "expires_at", "Expires date (optional)", "date", "", false);
  workspaceField(cf, "verification_reference", "Verification / commitment reference");
  workspaceField(cf, "terms_note", "Terms / notes", "textarea", "", false);
  workspaceSubmit(cf, "Save capital availability");
  cf.addEventListener("submit", e => {
    e.preventDefault();
    const v = values(cf);
    v.markets = v.markets ? v.markets.split("\n").map(x => x.trim()).filter(Boolean) : [];
    v.strategies = v.strategies === "both" ? ["assignment","resale"] : v.strategies === "any" ? [] : [v.strategies];
    v.max_commitment = Number(v.max_commitment);
    v.available_amount = Number(v.available_amount);
    v.verified_at = commitmentDate(v.verified_at);
    v.expires_at = commitmentDate(v.expires_at);
    v.status = "active";
    v.terms = v.terms_note ? {note: v.terms_note} : {};
    delete v.terms_note;
    runForm(cf, () => api("/api/commitments/capital", v), "Capital availability saved.", true);
  });
  capitalDetails.append(cf); box.append(capitalDetails);

  const plans = workspaceDetails("Shared demand search plans", false);
  if (!graph.search_plans?.length) {
    plans.append(node("p","No shared search plans yet.","muted small"));
  } else {
    plans.append(node("p","Identical buyer demand is grouped so one provider request can serve multiple mandates instead of spending API quota repeatedly.","muted small"));
    graph.search_plans.forEach(plan => {
      const q=plan.query_signature;
      const card=node("article",undefined,"evidence-row");
      card.append(node("strong", q.market + " · " + plan.demand_count + " demand path(s)"));
      card.append(node("p", "Max " + amount("money",q.max_total_price) + " · " + (q.property_types.length ? q.property_types.map(readable).join(", ") : "any property type") + " · priority " + plan.priority, "small"));
      if(plan.demand_count>1) card.append(node("p","Shared search saves duplicate provider requests for " + plan.demand_count + " matching buyer intents.","muted small"));
      plans.append(card);
    });
  }
  box.append(plans);

  const intents = workspaceDetails("Demand-first search intents", false);
  if (!graph.search_intents?.length) {
    intents.append(node("p","No active mandate search intents yet.","muted small"));
  } else {
    intents.append(node("p","These are provider-neutral search instructions generated from current buyer commitments. Future data adapters can consume the same intent shape without changing the core matching logic.","muted small"));
    graph.search_intents.forEach(item => {
      const buyer = state.buyers.find(x => x.id === item.buyer_id);
      const card = node("article", undefined, "evidence-row");
      card.append(node("strong", item.market + " · " + (buyer ? buyer.name : "Buyer")));
      card.append(node("p", item.strategies.map(readable).join(", ") + " · max " + amount("money", item.max_total_price) + " · priority " + item.priority, "small"));
      card.append(node("p", item.property_types.length ? item.property_types.map(readable).join(", ") : "Any recorded property type", "muted small"));
      const filters = item.filters || {};
      const filterText = Object.entries(filters).map(([k,v]) => readable(k) + " " + v).join(" · ");
      if (filterText) card.append(node("p", "Property filters: " + filterText, "muted small"));
      const route = state.provider_integrations?.routing?.find(x => x.search_intent_id === item.intent_id);
      if (route) card.append(node("p", route.selected_provider_id ? "Recommended provider: " + readable(route.selected_provider_id) : "No configured provider currently satisfies this intent; use reviewed CSV import as the fallback.", "muted small"));
      intents.append(card);
    });
  }
  box.append(intents);

  const budgetQueue = workspaceDetails("Search budget queue", false);
  const queuedSearches = state.provider_integrations?.search_queue || [];
  if (!queuedSearches.length) {
    budgetQueue.append(node("p","No buyer-demand searches are queued yet.","muted small"));
  } else {
    budgetQueue.append(node("p","ClubSP prioritizes limited provider requests by shared buyer demand, mandate priority, refresh need, and provider availability. This is an operational budget score—not a closing probability.","muted small"));
    queuedSearches.slice(0,10).forEach(item => {
      const representative = graph.search_intents.find(x => x.intent_id === item.search_intent_ids[0]);
      const card=node("article",undefined,"evidence-row");
      card.append(node("strong", (representative ? representative.market : "Search") + " · budget priority " + item.budget_priority_score + "/100"));
      card.append(node("p", item.demand_count + " demand path(s) · action " + readable(item.action) + (item.provider_id ? " · " + readable(item.provider_id) : ""), "small"));
      if(item.inventory_count!=null) card.append(node("p","Inventory preflight: " + item.inventory_count + " matching properties · " + item.inventory_per_demand_path + " per demand path","small"));
      if(item.capital_required) card.append(node("p","Resale capital check: " + item.capital_path_count + " current path(s) covering up to " + amount("money",item.required_capital),"small"));
      if(item.action==="capital_gap") card.append(node("p","Do not spend sourcing budget yet: this resale search has no current recorded capital path covering the buyer ceiling plus repair allowance.","muted small"));
      if(item.action==="no_inventory") card.append(node("p","Do not spend a record-fetch request on this query until demand criteria or inventory changes.","muted small"));
      if(item.action==="refine_query") card.append(node("p","Inventory is very broad; refine the buyer/search criteria before spending record-fetch requests.","muted small"));
      const r=item.rationale;
      card.append(node("p","Demand " + r.demand_points + " · mandate priority " + r.mandate_priority_points + " · refresh need " + r.refresh_need_points + " · provider availability " + r.provider_available_points,"muted small"));
      budgetQueue.append(card);
    });
  }
  box.append(budgetQueue);

  const preflightBox = workspaceDetails("Inventory preflight", false);
  const preflightProvider = (state.provider_integrations?.preflight_providers || []).find(x => x.id === "realestateapi");
  if (!preflightProvider) {
    preflightBox.append(node("p","No inventory-count provider is configured in this build.","muted small"));
  } else {
    preflightBox.append(node("p",
      preflightProvider.name + " · " + (preflightProvider.configured ? "credential configured" : "credential not configured") +
      " · remaining local count requests " + preflightProvider.remaining_local_requests,
      "muted small"
    ));
    preflightBox.append(node("p","Count mode estimates how much inventory matches buyer demand before ClubSP spends requests pulling records. Provider billing still depends on your connected plan, so each count call is explicit.","muted small"));
    if (graph.search_plans?.length) {
      const cf=node("form",undefined,"workspace-form provider-preflight-form");
      const countOptions=graph.search_plans.map(plan=>{
        const representative=graph.search_intents.find(x=>x.intent_id===plan.search_intent_ids[0]);
        return [representative.intent_id,representative.market+" · "+plan.demand_count+" demand path(s) · max "+amount("money",representative.max_total_price)];
      });
      workspaceSelect(cf,"search_intent_id","Buyer-derived search plan",countOptions);
      const confirmLabel=node("label",undefined,"check-label"), confirm=node("input");
      confirm.type="checkbox";confirm.name="confirm_external_request";confirm.required=true;
      confirmLabel.append(confirm,document.createTextNode(" I authorize this provider count request and understand billing depends on my provider plan."));
      const refreshLabel=node("label",undefined,"check-label"), refresh=node("input");
      refresh.type="checkbox";refresh.name="force_refresh";
      refreshLabel.append(refresh,document.createTextNode(" Force a fresh count instead of reusing a recent identical preflight."));
      cf.append(confirmLabel,refreshLabel);
      workspaceSubmit(cf,"Count matching inventory");
      if(!preflightProvider.configured || preflightProvider.remaining_local_requests<=0){
        Array.from(cf.elements).forEach(el=>el.disabled=true);
        preflightBox.append(node("p",!preflightProvider.configured?"Set REALESTATEAPI_API_KEY on the server to enable inventory preflight.":"Local preflight request cap reached.","muted small"));
      }
      cf.addEventListener("submit",e=>{
        e.preventDefault();
        const v=values(cf);
        v.provider_id="realestateapi";
        v.confirm_external_request=confirm.checked;
        v.force_refresh=refresh.checked;
        runForm(cf,()=>api("/api/providers/preflight",v),"Inventory preflight completed.");
      });
      preflightBox.append(cf);
    }
    const latest=state.provider_integrations?.preflight_results || [];
    latest.slice(0,10).forEach(item=>{
      const intent=graph.search_intents.find(x=>x.intent_id===item.search_intent_id);
      const card=node("article",undefined,"evidence-row");
      card.append(node("strong",(intent?intent.market:"Search")+" · "+(item.total_count==null?"count unavailable":item.total_count+" matching properties")));
      card.append(node("p","Counted "+new Date(item.created_at).toLocaleString()+" · inventory planning only; no candidate records were imported.","muted small"));
      preflightBox.append(card);
    });
  }
  box.append(preflightBox);

  const providerBox = workspaceDetails("External property search", false);
  const providers = state.provider_integrations?.providers || [];
  const provider = providers.find(x => x.id === "rentcast");
  if (!provider) {
    providerBox.append(node("p","No external property provider is configured in this build.","muted small"));
  } else {
    providerBox.append(node("p",
      provider.name + " · " + (provider.configured ? "credential configured" : "credential not configured") +
      " · local monthly cap " + provider.monthly_request_cap +
      " · remaining " + provider.remaining_local_requests,
      "muted small"
    ));
    const usage = provider.usage || {};
    const review = provider.review_metrics || {};
    providerBox.append(node("p",
      "This month: " + (usage.attempted_requests || 0) + " request(s) · " +
      (usage.staged_results || 0) + " staged result(s)" +
      (usage.results_per_successful_request == null ? "" : " · " + usage.results_per_successful_request.toFixed(1) + " results/successful request") +
      (usage.zero_result_rate == null ? "" : " · " + Math.round(usage.zero_result_rate * 100) + "% zero-result rate") +
      (review.reviewed_candidates ? " · " + Math.round(review.acceptance_rate * 100) + "% review acceptance" : "") +
      (review.deals_created ? " · " + review.deals_created + " downstream deal(s)" : "") +
      (review.closed_deals ? " · " + review.closed_deals + " recorded close(s)" : ""),
      "muted small"
    ));
    providerBox.append(node("p","Each search is explicit, cost-capped, and stages results for review. It never creates a deal or contacts a seller.","muted small"));
    if (graph.search_intents?.length) {
      const pf = node("form", undefined, "workspace-form provider-search-form");
      const searchOptions = (graph.search_plans?.length ? graph.search_plans.map(plan => {
        const representative = graph.search_intents.find(x => x.intent_id === plan.search_intent_ids[0]);
        return [representative.intent_id, representative.market + " · max " + amount("money",representative.max_total_price) + " · " + plan.demand_count + " demand path(s)"];
      }) : graph.search_intents.map(x => [x.intent_id, x.market + " · max " + amount("money",x.max_total_price) + " · priority " + x.priority]));
      workspaceSelect(pf, "search_intent_id", "Buyer-derived search plan", searchOptions);
      workspaceField(pf, "max_results", "Maximum listings to stage (1–50)", "number", "10");
      const confirmLabel=node("label",undefined,"check-label"), confirm=node("input");
      confirm.type="checkbox"; confirm.name="confirm_paid_request"; confirm.required=true;
      confirmLabel.append(confirm,document.createTextNode(" I authorize this external API request and understand it may count toward provider usage/billing."));
      const refreshLabel=node("label",undefined,"check-label"), refresh=node("input");
      refresh.type="checkbox"; refresh.name="force_refresh";
      refreshLabel.append(refresh,document.createTextNode(" Force a fresh provider request instead of reusing a recent identical search."));
      pf.append(confirmLabel, refreshLabel);
      workspaceSubmit(pf, "Search current listings");
      if (!provider.configured || provider.remaining_local_requests <= 0) {
        Array.from(pf.elements).forEach(el => el.disabled=true);
        providerBox.append(node("p", !provider.configured ? "Set RENTCAST_API_KEY on the server to enable live searches." : "Local monthly request cap reached.", "muted small"));
      }
      pf.addEventListener("submit", e => {
        e.preventDefault();
        const v=values(pf);
        v.provider_id="auto";
        v.max_results=Number(v.max_results);
        v.confirm_paid_request=confirm.checked;
        v.force_refresh=refresh.checked;
        runForm(pf,()=>api("/api/providers/search",v),"Provider search completed. Results were staged for review.");
      });
      providerBox.append(pf);
    } else {
      providerBox.append(node("p","Create an active standing buyer mandate first; ClubSP searches from demand, not from a generic lead list.","muted small"));
    }
  }
  box.append(providerBox);

  const reverse = workspaceDetails("Reverse Opportunity Search", graph.reverse_opportunities?.length > 0);
  if (!graph.reverse_opportunities?.length) {
    reverse.append(node("p","No reviewed research candidates currently match a standing buyer mandate.","muted small"));
  } else {
    reverse.append(node("p","These candidates were surfaced because current buyer demand already exists. They are still research candidates—not verified deals.","muted small"));
    graph.reverse_opportunities.forEach(item => {
      const card = node("article", undefined, "evidence-row");
      const best = item.best_match;
      card.append(node("strong", item.address + " · " + item.commitment_match_count + " buyer mandate(s)"));
      card.append(node("p", item.market + (item.property_type ? " · " + readable(item.property_type) : "") + " · buyer-demand fit " + item.best_commitment_score + "/100", "small"));
      card.append(node("p", "Best path: " + best.buyer_name + (best.buyer_company ? " · " + best.buyer_company : "") + " · max " + amount("money", best.max_total_price), "small"));
      card.append(node("p", best.reasons.join(" · "), "muted small"));
      const open = node("button","Open research file","button secondary"); open.type="button";
      open.addEventListener("click",()=>{ selected=item.property_id; selectedDeal=null; render(); $("property-title").scrollIntoView({block:"start"}); });
      card.append(open); reverse.append(card);
    });
  }
  box.append(reverse);

  const mandates = workspaceDetails("Standing demand", activeMandates > 0);
  if (!graph.buyer_mandates.length) mandates.append(node("p","No buyer mandates recorded yet.","muted small"));
  graph.buyer_mandates.forEach(m => {
    const b = state.buyers.find(x => x.id === m.buyer_id);
    const card = node("article", undefined, "evidence-row");
    card.append(node("strong", (b ? b.name : "Buyer") + " · " + m.name));
    card.append(node("p", m.markets.join(" · ") + " · " + m.strategies.join(", ") + " · max " + amount("money",m.max_total_price), "small"));
    card.append(node("p", "Priority " + m.priority + " · " + m.status + " · verified " + new Date(m.verified_at).toLocaleDateString() + (m.expires_at ? " · expires " + new Date(m.expires_at).toLocaleDateString() : ""), "muted small"));
    card.append(node("p", "Demand capacity: " + m.active_reservations + "/" + m.max_active_reservations + " active reservation(s) · " + m.available_reservation_slots + " slot(s) available · target " + m.target_units_per_month + "/month", "small"));
    const filters = m.filters || {};
    if (Object.keys(filters).length) card.append(node("p", "Filters: " + Object.entries(filters).map(([k,v]) => readable(k) + " " + v).join(" · "), "muted small"));
    const mfresh = commitmentFreshness(m, 90);
    card.append(node("p", mfresh.current ? "Demand verification current" : "Demand verification stale · excluded from sourcing/matching", mfresh.current ? "small" : "muted small"));
    card.append(commitmentReconfirmControl("buyer-mandates", m, 90));
    card.append(commitmentStatusControl("buyer-mandates", m, ["active","paused","expired"]));
    mandates.append(card);
  });
  box.append(mandates);

  const reservationBox = workspaceDetails("Reserve buyer demand for a deal", false);
  const reservableMandates = graph.buyer_mandates.filter(m => m.status === "active" && m.available_reservation_slots > 0 && commitmentFreshness(m,90).current);
  const unreservedDeals = (state.deals || []).filter(d => !graph.reservations?.some(r => r.deal_id === d.id && r.effective_status === "active"));
  if (!reservableMandates.length || !unreservedDeals.length) {
    reservationBox.append(node("p",
      !reservableMandates.length
        ? "No current buyer mandate has an available reservation slot."
        : "Every current deal is already reserved or there are no deals to reserve.",
      "muted small"
    ));
  } else {
    const rf=node("form",undefined,"workspace-form commitment-reservation-form");
    workspaceSelect(rf,"deal_id","Deal",unreservedDeals.map(d=>[d.id,d.address+" · "+readable(d.stage)]));
    workspaceSelect(rf,"mandate_id","Buyer commitment",reservableMandates.map(m=>{
      const b=state.buyers.find(x=>x.id===m.buyer_id);
      return [m.id,(b?b.name:"Buyer")+" · "+m.name+" · "+m.available_reservation_slots+" slot(s)"];
    }));
    workspaceField(rf,"expires_at","Reservation expires","date");
    workspaceField(rf,"evidence_reference","Reservation evidence / buyer confirmation");
    workspaceField(rf,"note","Reservation notes","textarea","",false);
    workspaceSubmit(rf,"Reserve buyer slot");
    rf.addEventListener("submit",e=>{
      e.preventDefault();
      const v=values(rf);
      v.expires_at=commitmentDate(v.expires_at);
      runForm(rf,()=>api("/api/commitments/reservations",v),"Buyer demand reserved for this deal.");
    });
    reservationBox.append(node("p","A reservation converts general buyer demand into a deal-specific hold and prevents ClubSP from overbooking that mandate's active capacity.","muted small"),rf);
  }
  (graph.reservations || []).filter(r=>r.effective_status==="active").forEach(r=>{
    const deal=state.deals.find(d=>d.id===r.deal_id);
    const buyer=state.buyers.find(b=>b.id===r.buyer_id);
    const card=node("article",undefined,"evidence-row");
    card.append(node("strong",(deal?deal.address:"Deal")+" · reserved for "+(buyer?buyer.name:"Buyer")));
    card.append(node("p","Mandate: "+r.mandate_name+" · expires "+new Date(r.expires_at).toLocaleDateString(),"small"));
    const d=workspaceDetails("Release reservation",false);
    const form=node("form",undefined,"workspace-form reservation-release-form");
    workspaceField(form,"evidence_reference","Release evidence reference");
    workspaceField(form,"note","Release reason","textarea");
    workspaceSubmit(form,"Release buyer slot");
    form.addEventListener("submit",e=>{
      e.preventDefault();
      runForm(form,()=>api("/api/commitments/reservations/"+r.id+"/release",values(form)),"Buyer reservation released.");
    });
    d.append(form);card.append(d);reservationBox.append(card);
  });
  box.append(reservationBox);

  const capitalList = workspaceDetails("Capital availability", activeCapital > 0);
  if (!graph.capital_profiles.length) capitalList.append(node("p","No capital profiles recorded yet.","muted small"));
  graph.capital_profiles.forEach(cp => {
    const card = node("article", undefined, "evidence-row");
    card.append(node("strong", cp.name + " · " + readable(cp.provider_type)));
    card.append(node("p", "Available " + amount("money", cp.available_amount) + " of " + amount("money", cp.max_commitment) + (cp.markets.length ? " · " + cp.markets.join(" · ") : " · any recorded market"), "small"));
    card.append(node("p", cp.status + " · verified " + new Date(cp.verified_at).toLocaleDateString() + (cp.expires_at ? " · expires " + new Date(cp.expires_at).toLocaleDateString() : ""), "muted small"));
    const cfresh = commitmentFreshness(cp, 30);
    card.append(node("p", cfresh.current ? "Capital verification current" : "Capital verification stale · excluded from current funding paths", cfresh.current ? "small" : "muted small"));
    card.append(commitmentReconfirmControl("capital", cp, 30));
    card.append(commitmentStatusControl("capital", cp, ["active","paused","unverified","expired"]));
    capitalList.append(card);
  });
  box.append(capitalList);

  const outcomeDetails = workspaceDetails("Record deal outcome", false);
  const of = node("form", undefined, "workspace-form commitment-outcome-form");
  const activeDeals = state.deals || [];
  workspaceSelect(of, "deal_id", "Deal", activeDeals.map(d => [d.id, d.address + " · " + readable(d.stage)]));
  workspaceSelect(of, "outcome", "Outcome", [
    ["closed","Closed"],["lost","Lost"],["withdrawn","Withdrawn"],["buyer_declined","Buyer declined"],
    ["funding_failed","Funding failed"],["title_failed","Title failed"],["seller_changed","Seller changed"],
    ["no_response","No response"],["other","Other"]
  ]);
  workspaceField(of, "reason_code", "Reason code / short cause");
  workspaceSelect(of, "buyer_id", "Buyer (optional)", [["","None"]].concat(state.buyers.map(b => [b.id,b.name + (b.company ? " · " + b.company : "")])));
  workspaceSelect(of, "buyer_mandate_id", "Buyer mandate (optional)", [["","None"]].concat(graph.buyer_mandates.map(m => {
    const b=state.buyers.find(x=>x.id===m.buyer_id); return [m.id,(b?b.name:"Buyer") + " · " + m.name];
  })));
  workspaceSelect(of, "capital_profile_id", "Capital path (optional)", [["","None"]].concat(graph.capital_profiles.map(cp => [cp.id,cp.name])));
  workspaceField(of, "evidence_reference", "Outcome evidence reference");
  workspaceField(of, "note", "Outcome notes", "textarea", "", false);
  workspaceSubmit(of, "Record outcome");
  if (!activeDeals.length) Array.from(of.elements).forEach(el => el.disabled=true);
  of.addEventListener("submit", e => {
    e.preventDefault();
    const v=values(of);
    const deal=state.deals.find(d=>d.id===v.deal_id);
    if(!v.buyer_id) delete v.buyer_id;
    if(!v.buyer_mandate_id) delete v.buyer_mandate_id;
    if(!v.capital_profile_id) delete v.capital_profile_id;
    if(deal?.buyer_matches?.id) v.buyer_match_run_id=deal.buyer_matches.id;
    runForm(of,()=>api("/api/commitments/outcomes",v),"Deal outcome recorded. Reliability evidence updated.");
  });
  outcomeDetails.append(node("p","Record what actually happened so ClubSP can learn which buyers, mandates, capital paths, and sourcing routes perform. Historical rates remain descriptive, not predictive.","muted small"),of);
  box.append(outcomeDetails);

  const reliability = workspaceDetails("Network reliability evidence", false);
  reliability.append(node("p","Descriptive history only. These rates summarize recorded outcomes; they are not calibrated probabilities or guarantees.","muted small"));
  if (!graph.buyer_reliability?.some(x => x.recorded_outcomes)) {
    reliability.append(node("p","No buyer outcome history yet. Record disposition outcomes to build reliability evidence.","muted small"));
  }
  (graph.buyer_reliability || []).filter(x => x.recorded_outcomes).forEach(item => {
    const card = node("article", undefined, "evidence-row");
    const rate = Math.round(item.descriptive_close_rate * 100);
    card.append(node("strong", item.name + (item.company ? " · " + item.company : "")));
    card.append(node("p", item.closed_outcomes + " closed of " + item.recorded_outcomes + " recorded outcomes · descriptive close rate " + rate + "%", "small"));
    if (item.last_outcome) card.append(node("p","Last outcome: " + readable(item.last_outcome.outcome) + " · " + readable(item.last_outcome.reason_code),"muted small"));
    reliability.append(card);
  });
  (graph.capital_reliability || []).filter(x => x.recorded_outcomes).forEach(item => {
    const card = node("article", undefined, "evidence-row");
    const rate = Math.round(item.descriptive_close_rate * 100);
    card.append(node("strong", item.name + " · " + readable(item.provider_type)));
    card.append(node("p", item.closed_outcomes + " closed of " + item.recorded_outcomes + " recorded outcomes · " + item.funding_failed_outcomes + " funding failure(s) · descriptive close rate " + rate + "%", "small"));
    reliability.append(card);
  });
  box.append(reliability);

  const readiness = workspaceDetails("Deal Readiness", graph.deal_readiness.length > 0);
  if (!graph.deal_readiness.length) readiness.append(node("p","Start a deal to calculate a path-to-close readiness score.","muted small"));
  graph.deal_readiness.forEach(r => {
    const deal = state.deals.find(d => d.id === r.deal_id);
    const card = node("article", undefined, "evidence-row");
    card.append(node("strong", (deal ? deal.address : "Deal") + " · " + r.score + "/100 · " + r.label));
    const parts = Object.entries(r.components).map(([k,v]) => readable(k) + " " + v.score + "/" + v.max);
    card.append(node("p", parts.join(" · "), "small"));
    card.append(node("p", r.buyer_matches_current ? "Buyer-match evidence current" : (r.stored_buyer_match_count ? "Buyer-match evidence stale · re-run matching" : "Buyer matching not yet recorded"), "muted small"));
    card.append(node("p", r.active_reservation ? "Buyer demand reserved through " + new Date(r.active_reservation.expires_at).toLocaleDateString() : "No deal-specific buyer reservation recorded", r.active_reservation ? "small" : "muted small"));
    if (r.blockers.length) card.append(node("p", "Blockers: " + r.blockers.join(" · "), "muted small"));
    if (r.next_actions.length) card.append(node("p", "Next: " + r.next_actions.join(" · "), "muted small"));
    readiness.append(card);
  });
  readiness.append(node("p", graph.score_semantics, "muted small"));
  box.append(readiness);
}
