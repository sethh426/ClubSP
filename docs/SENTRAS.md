# ClubSP Sentras — Real-Time Source Intelligence Fabric

## Mission

A **Sentra** is a governed source-intelligence unit. It monitors or queries an external source, preserves provenance, normalizes useful evidence, detects changes, and hands candidates to ClubSP's existing review, buyer-match, and opportunity workflows.

The architecture target is **thousands of near-real-time sources**. The first 100 Sentras are a validation milestone, not the ceiling.

A Sentra is **not synonymous with a scraper**. Its acquisition method can be an official API, ArcGIS/Socrata/open-data API, Apify Actor, Crawlee HTTP crawler, Playwright/browser extraction, PDF/CSV/XLSX parser, webhook/feed, or controlled manual/licensed-provider import.

Prefer official structured APIs first. Use permitted extraction only when the required information is not available through a stable structured source.

## Completion plan — 12 sections

The tracked exit criteria and current operational gate are in [Sentra delivery gates](SENTRA_DELIVERY_GATES.md). The implemented definition, routing and observation contract is in [Sentra kernel](SENTRA_KERNEL.md). Registry row counts are not production source counts.

### 1. Sentra kernel and registry
Define the canonical Sentra contract, capabilities, source class, acquisition mode, jurisdiction, cadence, cost/rights controls, freshness, provenance, health state, and downstream handoff.

**Exit:** the registry can describe thousands of Sentras without provider-specific business logic.

### 2. Distributed execution adapters
Implement common executors for direct HTTP/API, ArcGIS, Apify, Crawlee/Cheerio, Playwright, file/PDF, and webhook/feed sources. Add execution envelopes, idempotency keys, bounded payloads, result digests, and worker-safe contracts.

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

### 8. Real-time event orchestration
Use persistent event streams, consumer groups, schedules, authenticated webhooks, durable jobs, idempotency, bounded retries/backoff, dead-letter handling, refresh policies, change-triggered runs, per-source budgets, backpressure and worker sharding.

**Exit:** approved Sentras operate continuously, scale horizontally, and recover safely from failure.

### 9. MCP and agent capability plane
Expose bounded internal tools such as sentra.list, sentra.search, sentra.run, sentra.health, sentra.evidence, sentra.diff, sentra.route, and sentra.research. Connect Apify MCP for Actor discovery/execution while retaining ClubSP policy and audit gates.

**Exit:** AI agents can discover and use Sentras without bypassing source, cost, or action controls.

### 10. Intelligence, scoring, and learning
Track source yield, freshness, schema drift, duplicate rate, match rate, accepted evidence, downstream outcomes, latency, and cost. Learn routing preferences from observed source performance without representing small samples as calibrated profit predictions.

**Exit:** ClubSP gets better at deciding which Sentra to query and when.

### 11. Sentra Command Center
Replace the narrow sale-notice panel with a Sentra UI: health, last run, changes, candidates, matched buyers, evidence gaps, costs, alerts, source map, throughput, lag and drill-down provenance.

**Exit:** operator can understand and control the whole source fabric from one surface.

### 12. Scale rollout and production validation
Onboard in waves with contract tests, live canaries, source-rights notes, schema-drift alarms, cost ceilings, recovery drills, source replacement and portfolio-level coverage/yield review.

**Exit:** proven path from 10 to 100 to 1,000+ Sentras without redesigning the core contracts.

## Scale milestones

- **10 Sentras:** prove registry, adapters, provenance and change detection.
- **25 Sentras:** establish public-record breadth and cross-source identity.
- **50 Sentras:** add market/listing/enrichment and automatic routing.
- **100 Sentras:** production-hardening milestone.
- **250 Sentras:** shard workers by source family/jurisdiction and measure stream lag.
- **500 Sentras:** automatic health/schema-drift monitoring and source replacement.
- **1,000 Sentras:** distributed scheduling, event-driven ingestion and adaptive routing.
- **Thousands:** dynamic discovery and onboarding pipeline with human/policy approval before activation.

## Event-driven scale model

```text
Source / API / Actor / Feed
          |
          v
   Sentra Executor Fleet
          |
          v
  source.observed events
          |
          v
 Persistent Event Stream
          |
   +------+------+------+
   |             |      |
   v             v      v
Normalizer   Identity  Health
Workers      Resolver  Monitor
   |             |      |
   +-------> Evidence Graph
                 |
                 v
          Buyer-Match Router
                 |
                 v
          Opportunity Queue
```

For the first production scale tier, Redis Streams is preferred over plain Pub/Sub because source events need persistence, replay, consumer acknowledgement and worker-group distribution. The stream contract must remain transport-neutral so Kafka/Redpanda can replace Redis later without changing Sentra payload semantics.

## Technology routing

Recommended order:
1. Official API/open-data endpoint.
2. Existing ClubSP direct adapter.
3. Apify maintained Actor or vetted Actor with declared output.
4. Crawlee HTTP extraction for simple pages.
5. Playwright only for JS-dependent pages.
6. File parser for authoritative PDFs/CSV/XLSX.
7. Human review when source structure or meaning is ambiguous.

Apify can provide Actor tasks, schedules, datasets, API execution, webhooks and MCP. ClubSP still owns source registry, normalization, cost policy, provenance, dedupe, evidence review and downstream authorization.

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
