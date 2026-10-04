"use strict";

function commitmentDate(value) {
  if (!value) return "";
  return new Date(value + "T00:00:00Z").toISOString();
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
      intents.append(card);
    });
  }
  box.append(intents);

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
    providerBox.append(node("p","Each search is explicit, cost-capped, and stages results for review. It never creates a deal or contacts a seller.","muted small"));
    if (graph.search_intents?.length) {
      const pf = node("form", undefined, "workspace-form provider-search-form");
      workspaceSelect(pf, "search_intent_id", "Buyer-derived search intent",
        graph.search_intents.map(x => [x.intent_id, x.market + " · max " + amount("money",x.max_total_price) + " · priority " + x.priority]));
      workspaceField(pf, "max_results", "Maximum listings to stage (1–25)", "number", "10");
      const confirmLabel=node("label",undefined,"check-label"), confirm=node("input");
      confirm.type="checkbox"; confirm.name="confirm_paid_request"; confirm.required=true;
      confirmLabel.append(confirm,document.createTextNode(" I authorize this external API request and understand it may count toward provider usage/billing."));
      pf.append(confirmLabel);
      workspaceSubmit(pf, "Search current listings");
      if (!provider.configured || provider.remaining_local_requests <= 0) {
        Array.from(pf.elements).forEach(el => el.disabled=true);
        providerBox.append(node("p", !provider.configured ? "Set RENTCAST_API_KEY on the server to enable live searches." : "Local monthly request cap reached.", "muted small"));
      }
      pf.addEventListener("submit", e => {
        e.preventDefault();
        const v=values(pf);
        v.provider_id="rentcast";
        v.max_results=Number(v.max_results);
        v.confirm_paid_request=confirm.checked;
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
    card.append(commitmentStatusControl("buyer-mandates", m, ["active","paused","expired"]));
    mandates.append(card);
  });
  box.append(mandates);

  const capitalList = workspaceDetails("Capital availability", activeCapital > 0);
  if (!graph.capital_profiles.length) capitalList.append(node("p","No capital profiles recorded yet.","muted small"));
  graph.capital_profiles.forEach(cp => {
    const card = node("article", undefined, "evidence-row");
    card.append(node("strong", cp.name + " · " + readable(cp.provider_type)));
    card.append(node("p", "Available " + amount("money", cp.available_amount) + " of " + amount("money", cp.max_commitment) + (cp.markets.length ? " · " + cp.markets.join(" · ") : " · any recorded market"), "small"));
    card.append(node("p", cp.status + " · verified " + new Date(cp.verified_at).toLocaleDateString() + (cp.expires_at ? " · expires " + new Date(cp.expires_at).toLocaleDateString() : ""), "muted small"));
    card.append(commitmentStatusControl("capital", cp, ["active","paused","unverified","expired"]));
    capitalList.append(card);
  });
  box.append(capitalList);

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
    if (r.blockers.length) card.append(node("p", "Blockers: " + r.blockers.join(" · "), "muted small"));
    if (r.next_actions.length) card.append(node("p", "Next: " + r.next_actions.join(" · "), "muted small"));
    readiness.append(card);
  });
  readiness.append(node("p", graph.score_semantics, "muted small"));
  box.append(readiness);
}
