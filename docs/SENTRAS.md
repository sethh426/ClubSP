# ClubSP Sentras — 100-Source Intelligence Program

## Mission

A **Sentra** is a governed source-intelligence unit. It monitors or queries an external source, preserves provenance, normalizes useful evidence, detects changes, and hands candidates to ClubSP's existing review, buyer-match, and opportunity workflows.

A Sentra is **not synonymous with a scraper**. Its acquisition method can be:
- official API;
- ArcGIS/Socrata/open-data API;
- Apify Actor;
- Crawlee HTTP crawler;
- Playwright/browser extraction;
- PDF/CSV/XLSX parser;
- webhook/feed;
- manual or licensed-provider import.

Prefer official structured APIs first. Use permitted extraction only when the required information is not available through a stable structured source.

## Completion plan — 12 sections

### 1. Sentra kernel and registry
Define the canonical Sentra contract, capabilities, source class, acquisition mode, jurisdiction, cadence, cost/rights controls, freshness, provenance, health state, and downstream handoff. Create the 100-Sentra portfolio model.

**Exit:** registry can describe all current and future Sentras without provider-specific business logic.

### 2. Execution adapters
Implement common executors for direct HTTP/API, ArcGIS, Apify, Crawlee/Cheerio, Playwright, file/PDF, and webhook/feed sources.

**Exit:** a Sentra declares *how* it runs; business logic does not care which technology performed acquisition.

### 3. Normalization and evidence contracts
Create typed source-event, property-identity, sale-event, distress-event, market-evidence, buyer-signal, document, and change-event schemas. Add hashes, source timestamps, confidence/verification state, and evidence lineage.

**Exit:** all source families emit stable normalized records with raw-evidence references.

### 4. Government and public-record Sentras
Scale county/city/state sources: assessor, recorder, tax, sheriff, tax sale, land bank, surplus property, permits/code enforcement, court/public notices, GIS/open data.

**Exit:** first high-trust public-record family operates end-to-end.

### 5. Listing, auction, and market Sentras
Connect permitted listing, auction, REO/HUD, comparable-sale, rental, market, and property-data providers.

**Exit:** demand-first candidate discovery spans public and commercial sources.

### 6. Enrichment and cross-verification graph
Resolve address/APN/property identity, deduplicate records across Sentras, connect event history, detect conflicts, and select corroborating evidence.

**Exit:** ClubSP understands that records from different sources refer to the same property/event.

### 7. Buyer-demand matching and opportunity synthesis
Compile standing buyer mandates into Sentra search intents, route queries by coverage/cost, rank research readiness, and surface explainable matches without inventing value/profit.

**Exit:** Sentras search for demand, not indiscriminate lead volume.

### 8. Automation and event orchestration
Add schedules, authenticated webhooks, durable jobs, idempotency, bounded retries/backoff, dead-letter handling, refresh policies, change-triggered runs, and per-source budgets.

**Exit:** approved Sentras operate continuously and recover safely from failure.

### 9. MCP and agent capability plane
Expose bounded internal tools such as sentra.list, sentra.search, sentra.run, sentra.health, sentra.evidence, sentra.diff, sentra.route, and sentra.research. Connect Apify MCP for Actor discovery/execution while retaining ClubSP policy and audit gates.

**Exit:** AI agents can discover and use Sentras without bypassing source, cost, or action controls.

### 10. Intelligence, scoring, and learning
Track source yield, freshness, schema drift, duplicate rate, match rate, accepted evidence, downstream outcomes, latency, and cost. Learn routing preferences from observed source performance without representing small samples as calibrated profit predictions.

**Exit:** ClubSP gets better at deciding which Sentra to query and when.

### 11. Sentra Command Center
Replace the narrow sale-notice panel with a Sentra UI: health, last run, changes, candidates, matched buyers, evidence gaps, costs, alerts, source map, and drill-down provenance.

**Exit:** operator can understand and control the whole source network from one surface.

### 12. 100-Sentra rollout and production validation
Onboard in waves with contract tests, live canaries, source-rights notes, schema-drift alarms, cost ceilings, recovery drills, and portfolio-level coverage/yield review.

**Exit:** 100 registered Sentras, each either production-active, verified standby, or intentionally disabled with a recorded reason.

## Target portfolio: 100 Sentras

The count is a portfolio target, not a mandate to scrape 100 websites. Multiple Sentras can use APIs, feeds, public datasets, or licensed providers.

| Family | Target |
|---|---:|
| County/city property + GIS/assessor/recorder | 18 |
| Sheriff/foreclosure/tax-sale/public auction | 14 |
| Land bank/surplus/redevelopment/public property | 8 |
| Permits/code enforcement/municipal distress | 8 |
| Court/public notices/probate where lawful and useful | 7 |
| Listings/REO/HUD/auction marketplaces | 10 |
| Comparable sales/property/valuation evidence | 8 |
| Rental/market/neighborhood/economic signals | 7 |
| Buyer/investor demand signals | 7 |
| Funding/title/closing/transaction support evidence | 5 |
| Regulatory/compliance/reference intelligence | 4 |
| Meta-Sentras: source discovery, health, schema drift, coverage | 4 |
| **Total** | **100** |

## Wave rollout

- **Wave A — 10 Sentras:** prove registry, adapters, provenance and change detection.
- **Wave B — 25 Sentras:** establish public-record breadth and cross-source identity.
- **Wave C — 50 Sentras:** add market/listing/enrichment and automatic routing.
- **Wave D — 75 Sentras:** add buyer/funding/operations signals and portfolio telemetry.
- **Wave E — 100 Sentras:** production hardening, coverage balancing, health automation and source replacement.

## Initial Sentras

1. `allen_county_accdc` — ACCDC property availability.
2. `allen_county_north_campus` — North Campus sale notice.
3. `allen_county_sheriff_sales` — sheriff foreclosure-sale notices.
4. `allen_county_imap_parcel` — parcel/owner-of-record evidence.
5. `allen_county_sales` — completed-sales evidence.
6. `apify_public_foreclosure` — candidate Actor family for official foreclosure/sheriff sources.
7. `apify_arcgis_parcels` — candidate ArcGIS parcel-normalization Actor family.
8. `rentcast_sale_listings` — demand-first active listing discovery.
9. `realestateapi_inventory_preflight` — buyer-demand inventory count/preflight.
10. `federal_register_real_estate_rules` — regulatory/reference monitor when relevant.

Items 6, 7 and 10 remain **planned** until actor/source quality, terms, cost, and exact use case are reviewed.

## Technology routing

Recommended order:
1. Official API/open-data endpoint.
2. Existing ClubSP direct adapter.
3. Apify maintained Actor or vetted Actor with declared output.
4. Crawlee Cheerio/HTTP extraction for simple pages.
5. Playwright only for JS-dependent pages.
6. File parser for authoritative PDFs/CSV/XLSX.
7. Human review when source structure or meaning is ambiguous.

Apify can provide Actor tasks, schedules, datasets, API execution, webhooks and MCP. ClubSP must still own the source registry, normalization, cost policy, provenance, dedupe, evidence review and downstream authorization.

## Required controls for every production Sentra

- stable Sentra ID and source owner;
- source URL/endpoint and acquisition mode;
- declared capabilities and jurisdiction;
- terms/rights note;
- credential reference by environment variable only;
- request/result cost ceiling;
- cadence and freshness target;
- timeout and response-size limits;
- normalized schema version;
- provenance digest and source timestamp;
- idempotency/deduplication key;
- schema-drift detection;
- health and last-success metadata;
- raw evidence reference or bounded excerpt;
- explicit downstream permissions;
- test fixture and contract test;
- kill switch.

## Non-goals

Sentras do not automatically create deals, invent seller motivation, infer contact permission, make offers, sign contracts, move money, or turn assessed/provider values into underwriting truth. Those remain behind ClubSP's existing evidence and authorization gates.
