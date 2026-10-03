"use strict";
document.addEventListener("DOMContentLoaded", () => {
  const output = document.getElementById("discovery-output");
  const form = document.getElementById("discovery-form");
  const result = document.getElementById("discovery-result");
  function node(tag, text) { const item = document.createElement(tag); item.textContent = text; return item; }
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
      for (const candidate of source.candidates) {
        card.append(node("h4", candidate.address), node("p", "Advertised minimum bid: $" + candidate.minimum_bid.toLocaleString("en-US")), node("p", candidate.availability), node("p", candidate.identity_note));
        card.append(node("p", "Bid window: " + candidate.bid_start + " to " + candidate.bid_end));
        card.append(node("p", "Parcels: " + candidate.parcel_ids.join(", ")));
        for (const gap of candidate.review_gaps) card.append(node("p", gap));
        if (candidate.within_recorded_price_limit === null) card.append(node("p", "Save your buy box to compare the advertised price against your limit."));
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
