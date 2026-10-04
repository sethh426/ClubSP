"use strict";

function sourcingReviewFields(form) {
  workspaceField(form, "reviewer", "Reviewer", "text");
  workspaceField(form, "evidence_reference", "Review evidence reference", "text");
  workspaceField(form, "note", "Review notes (identity, condition, location and sale validity)", "textarea");
}
function sourcingCheck(form, name, label) {
  const wrap = node("label", label), input = node("input"); input.type = "checkbox"; input.name = name;
  wrap.append(input); form.append(wrap); return input;
}
function renderSourcing() {
  const box = $("sourcing-workspace"); box.replaceChildren();
  const discovery = workspaceDetails("Research opportunities from reviewed candidates", true);
  discovery.append(node("p", "Ranked by saved buy-box fit and evidence completeness. These are research candidates, not verified deals or profit predictions.", "muted small"));
  if (!state.discovery.items.length) discovery.append(node("p", "No reviewed candidate properties yet. Import and accept a candidate row to begin.", "muted small"));
  state.discovery.items.forEach(item => {
    const card = node("article", undefined, "evidence-row discovery-card");
    card.append(node("strong", item.address + " · " + readable(item.decision) + " · " + item.score + "/100"));
    card.append(node("p", item.market + (item.property_type ? " · " + readable(item.property_type) : ""), "small"));
    if (item.commitment_match_count) {
      const best = item.commitment_matches[0];
      card.append(node("p", "Standing demand: " + item.commitment_match_count + " mandate(s) · best " + best.buyer_name + " " + best.score + "/100", "small"));
      card.append(node("p", best.reasons.join(" · "), "muted small"));
    } else {
      card.append(node("p", "Standing demand: none recorded for this candidate.", "muted small"));
    }
    card.append(node("p", item.reasons.length ? item.reasons.join(" · ") : "Ready for owner research", "muted small"));
    card.append(node("p", "Source: " + item.source.provider + " · as of " + item.source.as_of + ". No automatic economics.", "muted small"));
    const open = node("button", "Open research file", "button secondary"); open.type = "button";
    open.addEventListener("click", () => { selected = item.property_id; selectedDeal = null; render(); $("property-title").scrollIntoView({block: "start"}); });
    card.append(open); discovery.append(card);
  });
  box.append(discovery);
  const details = workspaceDetails("Import CSV (up to 50 rows / 45 KB)", !state.sourcing.batches.length);
  const form = node("form", undefined, "workspace-form sourcing-import-form");
  workspaceSelect(form, "kind", "Import type", [["candidates", "Candidate properties"], ["county_sales", "County completed sales"]]);
  workspaceField(form, "provider", "Source provider", "text");
  workspaceField(form, "source_url", "HTTPS source reference", "text");
  workspaceField(form, "source_date", "Source as-of date", "date");
  workspaceField(form, "rights_basis", "Your basis for using this source / export", "textarea");
  workspaceField(form, "city", "Confirmed city for these rows", "text");
  workspaceField(form, "state", "State (two-letter code)", "text");
  const csvInput = workspaceField(form, "csv", "CSV content", "textarea"); csvInput.maxLength = 45000;
  const fileLabel = node("label", "Or load a CSV file"), file = node("input"); file.type = "file"; file.accept = ".csv,text/csv";
  file.addEventListener("change", async () => {
    if (!file.files.length) return;
    if (file.files[0].size > 45000) { message("Split this CSV into files below 45 KB.", true); file.value = ""; return; }
    csvInput.value = await file.files[0].text();
  });
  fileLabel.append(file); form.append(fileLabel);
  details.append(node("p", "Candidate headers: address,zip,parcel_id,property_type. County sales headers: Parcel Number,Address,Sale Date,Sale Price,Living Area. Keep parcel IDs as text. Use one confirmed city per batch.", "muted small"));
  const link = node("a", "Open Allen County sales viewer"); link.href = "https://acimap.us/comps/"; link.target = "_blank"; link.rel = "noopener noreferrer";
  details.append(link, node("p", "Export covers displayed rows only. Source dates and sales remain owner-supplied evidence; importing does not verify them.", "muted small"));
  workspaceSubmit(form, "Preview import");
  form.addEventListener("submit", event => { event.preventDefault(); runForm(form, () => api("/api/sourcing/import", values(form)), "Import staged for review. No properties or deals created automatically."); });
  details.append(form); box.append(details);
  state.sourcing.batches.forEach(batch => {
    const batchBox = workspaceDetails(batch.provider + " · " + readable(batch.kind) + " · as of " + batch.source_date);
    batchBox.append(node("p", "Source: " + batch.source_url + " · " + batch.row_count + " rows · imported " + batch.retrieved_at, "muted small"));
    state.sourcing.rows.filter(row => row.batch_id === batch.id).forEach(row => {
      const card = node("article", undefined, "evidence-row sourcing-row");
      card.append(node("strong", "Row " + row.line + " · " + (row.value.address || "Unknown address") + " · " + readable(row.status)));
      card.append(node("p", "Parcel " + (row.value.parcel_id || "unknown") + " · " + batch.city + ", " + batch.state, "small"));
      if (batch.discovery) {
        card.append(node("p", "Official notice minimum bid: " + amount("money", batch.discovery.candidate.minimum_bid) + ". This is an advertised floor, not negotiated seller terms.", "note"));
        card.append(node("p", batch.discovery.candidate.identity_note + " · " + batch.discovery.reviewer + ": " + batch.discovery.note, "small"));
        card.append(node("p", "Full notice parcels: " + batch.discovery.candidate.parcel_ids.join(", "), "small"));
      }
      if (batch.kind === "county_sales") card.append(node("p", "Sale " + (row.value.sale_date || "unknown") + " · " + amount("money", row.value.sale_price || 0) + " · " + (row.value.living_area || "unknown") + " sq ft · class " + (row.value.property_class || "unknown"), "small"));
      row.errors.forEach(error => card.append(node("p", error, "note")));
      if (row.status === "pending") {
        const review = node("form", undefined, "workspace-form sourcing-review-form");
        workspaceSelect(review, "action", "Decision", [["exclude", "Exclude"], ["accept", "Accept after review"]]);
        if (batch.kind === "county_sales") workspaceSelect(review, "property_id", "Subject property", [["", "Select subject"], ...state.properties.map(p => [p.id, p.address + " · " + p.city])]);
        sourcingReviewFields(review);
        const identity = sourcingCheck(review, "identity_confirmed", "I confirmed parcel, address and market identity");
        const sale = batch.kind === "county_sales" ? sourcingCheck(review, "sale_verified", "I checked sale validity, arm’s-length status and comparability") : null;
        workspaceSubmit(review, "Save import review");
        review.addEventListener("submit", event => {
          event.preventDefault(); const data = values(review); data.identity_confirmed = identity.checked;
          if (sale) data.sale_verified = sale.checked;
          runForm(review, () => api("/api/sourcing/rows/" + row.id + "/review", data), "Import review recorded.");
        });
        card.append(review);
      } else if (row.review) {
        card.append(node("p", row.review.reviewer + " · " + row.review.note + " · " + row.review.evidence_reference, "muted small"));
        if (row.review.property_id) {
          const open = node("button", "Open reviewed property", "button secondary"); open.type = "button";
          open.addEventListener("click", () => { selected = row.review.property_id; selectedDeal = null; render(); $("property-title").scrollIntoView({block: "start"}); }); card.append(open);
        }
      }
      batchBox.append(card);
    }); box.append(batchBox);
  });
  if (state.sourcing.sales.length) {
    const sales = workspaceDetails("Reviewed comparable sales");
    sales.append(node("p", "All accepted sales for a subject are captured with its next underwriting. Exit price remains a manual assumption; no automatic appraisal or averaging is performed.", "muted small"));
    state.sourcing.sales.forEach(sale => {
      const card = node("article", undefined, "evidence-row sale-evidence-row");
      const prop = state.properties.find(p => p.id === sale.property_id);
      card.append(node("strong", sale.sale.address + " → " + (prop?.address || sale.property_id)), node("p", sale.sale.sale_date + " · " + amount("money", sale.sale.sale_price) + " · " + readable(sale.status)), node("p", sale.review.note + " · " + sale.review.evidence_reference, "muted small"));
      if (sale.status === "accepted") {
        const withdraw = node("form", undefined, "workspace-form sale-withdraw-form"); sourcingReviewFields(withdraw); workspaceSubmit(withdraw, "Withdraw comparable");
        withdraw.addEventListener("submit", event => { event.preventDefault(); runForm(withdraw, () => api("/api/sourcing/sales/" + sale.id + "/withdraw", values(withdraw)), "Comparable withdrawn; review affected underwriting."); }); card.append(withdraw);
      } else card.append(node("p", sale.withdrawal.note + " · " + sale.withdrawal.evidence_reference, "muted small"));
      sales.append(card);
    }); box.append(sales);
  }
}
