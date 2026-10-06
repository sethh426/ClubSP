"use strict";
document.addEventListener("DOMContentLoaded", () => {
  const output = document.getElementById("discovery-output");
  const form = document.getElementById("discovery-form");
  const result = document.getElementById("discovery-result");
  function node(tag, text) { const item = document.createElement(tag); item.textContent = text; return item; }
  function intakeForm(source, candidate, index) {
    if (candidate.intake_supported === false) {
      const box = node("details", ""); box.append(node("summary", "Research only · parcel confirmation required"));
      box.append(node("p", "This official notice does not establish a parcel identity or purchase price. Confirm the parcel in Allen County records and verify the sale is still active before moving it into candidate intake."));
      for (const blocker of candidate.intake_blockers || []) box.append(node("p", blocker));
      return box;
    }
    const box = node("details", ""); box.append(node("summary", "Stage for identity review"));
    const blockers = candidate.intake_blockers || [];
    if (blockers.length) {
      for (const blocker of blockers) box.append(node("p", blocker));
      const policy = node("a", "Recheck the official source"); policy.href = "#discovery-form"; box.append(policy);
      return box;
    }
    const review = node("form", ""); review.className = "workspace-form";
    function field(name, label, tag = "input") {
      const wrap = node("label", label), input = node(tag, ""); input.name = name; input.required = true;
      wrap.append(input); review.append(wrap); return input;
    }
    const parcel = field("parcel_id", "Reviewed parcel named in this notice", "select");
    for (const id of candidate.parcel_ids) { const option = node("option", id); option.value = id; parcel.append(option); }
    field("zip", "Verified ZIP").pattern = "[0-9]{5}(-[0-9]{4})?";
    field("property_type", "Reviewed property type");
    field("reviewer", "Reviewer"); field("note", "Evidence and survey / parcel portion review notes", "textarea");
    const confirm = field("identity_confirmed", "I checked the address, market, selected parcel and surveyed portions"); confirm.type = "checkbox";
    const button = node("button", "Stage pending intake"); button.type = "submit"; button.className = "button";
    const status = node("p", ""); status.setAttribute("role", "status"); review.append(button, status);
    review.addEventListener("submit", async event => {
      event.preventDefault(); button.disabled = true;
      const data = Object.fromEntries(new FormData(review)); data.identity_confirmed = confirm.checked;
      data.check_id = source.id; data.candidate_index = index;
      try {
        const response = await fetch("/api/discovery/intake", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)});
        const saved = await response.json(); if (!response.ok) throw new Error(saved.error || "Intake failed");
        status.textContent = saved.duplicate ? "This notice already has a staged intake." : "Pending intake saved. No property or deal created.";
        const link = node("a", "Review staged intake"); link.href = "/?intake=" + encodeURIComponent(saved.id) + "#sourcing-workspace"; status.append(" ", link);
      } catch (error) { status.textContent = error.message; }
      finally { button.disabled = false; }
    });
    box.append(node("p", "All notice parcels and terms are retained. This selection does not establish ownership, seller authority, or that a whole parcel is offered."), review);
    return box;
  }
  async function reload() {
    const response = await fetch("/api/discovery");
    if (!response.ok) throw new Error("Notice status unavailable");
    const data = await response.json(); output.replaceChildren();
    for (const source of data.sources) {
      const card = node("article", ""); card.className = "panel gmail-preview";
      card.append(node("h3", source.name), node("p", source.status.replaceAll("_", " ") + (source.stale ? " · stale" : "")));
      if (source.fetched_at) card.append(node("p", "Checked: " + source.fetched_at));
      if (source.excerpt) card.append(node("p", source.excerpt));
      const link = node("a", "Review official source"); link.href = source.url; link.target = "_blank"; link.rel = "noopener noreferrer"; card.append(link);
      if (source.source_id === "sheriff_sales" && source.status === "scheduled_sales") {
        const resolve = node("button", "Resolve parcel IDs from Allen County GIS");
        resolve.type = "button"; resolve.className = "button";
        const resolveStatus = node("p", ""); resolveStatus.setAttribute("role", "status");
        resolve.addEventListener("click", async () => {
          resolve.disabled = true; resolveStatus.textContent = "Checking official Allen County GIS…";
          try {
            const response = await fetch("/api/discovery/resolve-parcels", {
              method:"POST", headers:{"Content-Type":"application/json"},
              body:JSON.stringify({check_id:source.id})
            });
            const saved = await response.json();
            if (!response.ok) throw new Error(saved.error || "Parcel resolution failed");
            resolveStatus.textContent = (saved.parcel_resolution_count || 0) + " candidate parcel(s) resolved from official GIS.";
            await reload();
          } catch (error) { resolveStatus.textContent = error.message; }
          finally { resolve.disabled = false; }
        });
        card.append(resolve, resolveStatus);
      }
      if (["source_access_blocked", "failed"].includes(source.status)) {
        const fallback = node("details", ""); fallback.append(node("summary", "Record reviewed official-source snapshot"));
        fallback.append(node("p", "Use this only with text copied from the configured official Allen County page. ClubSP will parse and save the snapshot with its reviewer and source URL; it still will not create a deal or authorize a bid."));
        const snapshot = node("form", ""); snapshot.className = "workspace-form discovery-snapshot-form";
        function snapField(name, label, tag = "input") {
          const wrap = node("label", label), input = node(tag, ""); input.name = name; input.required = true;
          wrap.append(input); snapshot.append(wrap); return input;
        }
        const url = snapField("source_url", "Official source URL"); url.value = source.url; url.readOnly = true;
        snapField("reviewer", "Reviewer");
        snapField("note", "How you verified this is the current official page", "textarea");
        const body = snapField("body", "Official page text", "textarea"); body.maxLength = 100000;
        const save = node("button", "Parse and save reviewed snapshot"); save.type = "submit"; save.className = "button";
        const feedback = node("p", ""); feedback.setAttribute("role", "status"); snapshot.append(save, feedback);
        snapshot.addEventListener("submit", async event => {
          event.preventDefault(); save.disabled = true;
          try {
            const data = Object.fromEntries(new FormData(snapshot));
            data.source_id = source.source_id;
            const response = await fetch("/api/discovery/snapshot", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)});
            const saved = await response.json(); if (!response.ok) throw new Error(saved.error || "Snapshot review failed");
            feedback.textContent = "Reviewed official snapshot saved. Recheck its candidate/availability details before intake.";
            await reload();
          } catch (error) { feedback.textContent = error.message; }
          finally { save.disabled = false; }
        });
        fallback.append(snapshot); card.append(fallback);
      }
      for (const [index, candidate] of source.candidates.entries()) {
        card.append(node("h4", candidate.address), node("p", candidate.availability), node("p", candidate.identity_note));
        if (candidate.minimum_bid !== null && candidate.minimum_bid !== undefined) {
          card.append(node("p", "Advertised minimum bid: $" + Number(candidate.minimum_bid).toLocaleString("en-US")));
        }
        if (candidate.judgment_amount !== null && candidate.judgment_amount !== undefined) {
          card.append(node("p", "Judgment amount (not a purchase price): $" + Number(candidate.judgment_amount).toLocaleString("en-US")));
        }
        if (candidate.bid_start && candidate.bid_end) card.append(node("p", "Bid window: " + candidate.bid_start + " to " + candidate.bid_end));
        if (candidate.sale_date) card.append(node("p", "Scheduled sheriff sale date: " + candidate.sale_date));
        if (candidate.cause_number) card.append(node("p", "Foreclosure cause: " + candidate.cause_number));
        if (candidate.source_document_url) {
          const documentLink = node("a", "Review official monthly sheriff-sale document");
          documentLink.href = candidate.source_document_url; documentLink.target = "_blank"; documentLink.rel = "noopener noreferrer";
          card.append(documentLink);
        }
        if (candidate.parcel_ids?.length) card.append(node("p", "Parcels: " + candidate.parcel_ids.join(", ")));
        if (candidate.parcel_resolution?.status === "resolved") {
          const evidence = candidate.parcel_resolution;
          card.append(node("p", "Official GIS match: " + evidence.official_address + " · PIN " + evidence.pin
            + (evidence.property_class ? " · " + evidence.property_class : "")
            + (evidence.year_built ? " · built " + evidence.year_built : ""), "small"));
        } else if (candidate.parcel_resolution?.reason) {
          card.append(node("p", "Parcel lookup: " + candidate.parcel_resolution.reason, "muted small"));
          for (const suggestion of candidate.parcel_resolution.review_suggestions || []) {
            card.append(node("p", "Review-only GIS suggestion: " + suggestion.official_address
              + " · PIN " + suggestion.pin + " · similarity " + suggestion.similarity, "muted small"));
          }
        }
        for (const gap of candidate.review_gaps) card.append(node("p", gap));
        card.append(node("h4", "Preliminary buyer criteria"));
        if (!candidate.buyer_criteria?.length) {
          card.append(node("p", "No active buyer criteria recorded. Research can continue without a purchase budget."));
          const buyerLink = node("a", "Add buyer criteria"); buyerLink.href = "#buyer-form"; card.append(buyerLink);
        }
        for (const buyer of candidate.buyer_criteria || []) {
          card.append(node("p", buyer.name + " · " + buyer.status.replaceAll("_", " ")));
          for (const reason of buyer.reasons) card.append(node("p", reason));
          card.append(node("p", "Funding status on record: " + buyer.funding_status_on_record + ". No buyer commitment is confirmed."));
        }
        card.append(intakeForm(source, candidate, index));
      }
      output.append(card);
    }
  }
  form.addEventListener("submit", async event => {
    event.preventDefault(); const button = form.querySelector("button"); button.disabled = true;
    result.textContent = "Checking one official notice…";
    try {
      const response = await fetch("/api/discovery/check", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({source_id: form.elements.source_id.value})});
      const data = await response.json(); if (!response.ok) throw new Error(data.error || "Notice check failed");
      result.textContent = data.cached ? "Saved check reused (24-hour cache)." : "Check saved. Review availability and terms before creating a deal.";
      await reload();
    } catch (error) { result.textContent = error.message; }
    finally { button.disabled = false; }
  });
  document.getElementById("discovery-reload").addEventListener("click", () => reload().catch(error => { result.textContent = error.message; }));
  reload().catch(error => { result.textContent = error.message; });
});
