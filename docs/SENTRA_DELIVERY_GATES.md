# Sentra delivery gates — the 12 sections

The target architecture scales beyond 100 sources. The 100-Sentra rollout is the first production validation milestone.

User-supplied `Rule.txt` sets the delivery rule: “Do not continue on a new part of the project until the first work is fully functional and operational.” Implementation and local tests are recorded separately from live operation. No later section is marked complete merely because definitions or mock tests exist.

The buyer-first guidance in user-supplied `pasted.txt` remains the business contract: learn real buyer markets, prices, property types, condition limits and exclusions; route source work to those needs; measure qualified matches and completed outcomes. Source count and lead-list size are not measures of revenue or demand.

## Current gate

Section 1's kernel code and synthetic runtime validation are implemented on top of the Meta-Sentra lifecycle prerequisite. The combined local suite passed **584 tests** on October 6, 2026. This count includes the existing application suite, not 584 live-source validations.

The [GitHub validation run](https://github.com/sethh426/ClubSP/actions/runs/37523701021) for implementation commit `e2cb11f2c25a67c1161d14e975d179efbad93384` passed Python 3.11–3.13, browser tests and the live public-catalog/ArcGIS-schema check. The live check established catalog/schema reachability on the GitHub runner; it did not activate sources or import property facts.

The development environment's local live-check command reported DNS lookup failures for ArcGIS and Apify, and Data.gov reported that its configured key was required there. Deployment of the actual configured service, an approved source-observation canary on that deployment host, and the reviewed real-evidence handoff remain open. Sections 2–12 remain open.

| # | Section | Current implementation basis | Required exit evidence |
| --- | --- | --- | --- |
| 1 | Kernel and registry | Validated static/dynamic definition, isolated snapshots, provenance/rights/cost/freshness fields, geographic routing, paginated API and durable raw observation reservations. | Pass definition and lifecycle contracts, restart and concurrency checks, and an approved observation canary on the deployment host. Preserve the actual definition and review references in each run. |
| 2 | Execution adapters | Existing public JSON/CSV/ArcGIS and fixed provider/PDF paths; credentialed and paid generalized execution remains gated. | For official APIs, ArcGIS, Apify, Crawlee/Cheerio, Playwright, files/PDFs and webhooks: demonstrate a bounded real acquisition with its immutable raw artifact, identity, timestamps, cost and failure contract. Actor start acknowledgement must not count as collected evidence. |
| 3 | Normalization and evidence | Existing memory/source records and staged provider evidence; generic results remain raw observations. | Every adapter produces the same versioned normalized envelope and typed entities. Contract fixtures must cover missing values, timestamps, malformed input, source lineage, corrections, duplicate events and raw-evidence links. |
| 4 | Government/public records | Existing Allen County parcel, sale-notice and sheriff-source paths plus Meta discovery/probing. | Complete reviewed extraction canaries for assessor, recorder, sheriff/tax sales, land-bank/surplus, permits and code sources. Page monitors, catalogs, notices and actual parcel records must be labeled by what they deliver. |
| 5 | Listings, auctions and markets | RentCast/RealEstateAPI provider integration and approved import paths exist; they are not equivalent to broad verified market coverage. | Connected provider rights/coverage/cost records; live listing, auction, completed-sale and market evidence contracts; expired/cancelled/withdrawn handling. A listing or asking price must not become a completed comparable sale. |
| 6 | Cross-verification graph | Existing parcel resolution, source evidence and conflict/readiness review provide a starting point. | Link independent Sentra records through jurisdiction-qualified parcel/property/event identity. Test split parcels, address ambiguity, ownership/price/date conflicts, duplicate sources and unresolved evidence. Never silently merge uncertain identities. |
| 7 | Buyer-demand intelligence | Standing mandates and provider-neutral search intents already exist. Kernel routing distinguishes declared from verified coverage. | Show an active buyer mandate becoming a constrained search, source route, normalized candidate and explained match. Apply hard exclusions before ranking; expired demand and unknown fields must stay explicit. Include accepted/declined buyer evidence. |
| 8 | Automation engine | Existing durable Meta-Sentra discovery/health leases and kernel idempotency/allowances; full distributed operations are unfinished. | Exercise schedules/timezones, unified acquisition budgets, bounded retries, crash/lease recovery, change detection, queue persistence, webhook deduplication and dead letters. Prove concurrent workers cannot duplicate effects or exceed an aggregate ceiling. |
| 9 | MCP and agents | HTTP application routes exist; an MCP transport is not implemented by them. | Validated tools for `sentra.search`, `.run`, `.diff`, `.health`, `.evidence` and `.route`; bounded schemas, authentication, evidence references and errors. Read/search calls must not silently execute paid acquisitions or downstream actions. |
| 10 | Learning/router intelligence | Existing recorded observations, prediction/outcome learning and commitment outcomes; current route order is transparent policy. | Store source-selection features and actual accepted evidence, freshness, matches, costs and outcomes. Test feedback attribution, sparse data, evaluation/holdout and source failure. Historical fit must not be presented as calibrated profit or closing probability. |
| 11 | Sentra Command Center | Existing Revenue Command Center and new canonical Sentra state API; no new Sentra dashboard has been claimed. | Render measured health/activity, buyer matches, costs, changes and evidence gaps. Exercise source pause/review/re-quarantine controls, persistence, empty/error states, desktop/mobile use and guarded actions against the real API. |
| 12 | Rollout and production validation | Architecture and rollout gate design; counts are not yet a production certification. | At 10 → 25 → 50 → 75 → 100, archive per-source contract tests and host-specific live canaries, rights/coverage/cost configuration, identity/evidence samples and health history. Promote only passing sources; document rollback and blocked sources. |

## Rollout counting

A countable production Sentra is one concrete governed source/endpoint with an implemented acquisition path, valid schema/normalization, approved rights and cost configuration, explicit geographic coverage, a passing live canary and current health. Planned families, Meta discovery catalogs, internal health services, duplicate endpoint definitions and synthetic fixtures do not increase this count.

Each stage must show the count of qualifying concrete sources, not the number of registry rows. Validation also needs representative buyer searches, identity conflicts, an unavailable source, changed schema, duplicate webhook/run, process restart and a cost-ceiling refusal. Stage growth pauses when a preceding operational gate fails.

The first-25 source work and the Evidence Compiler, Temporal Intelligence Engine and Shadow Intelligence Network spike must consume these contracts when their prerequisites pass. This document does not certify those separate workstreams as finished.

## Review artifacts

- [Kernel contract and HTTP interfaces](SENTRA_KERNEL.md)
- [Meta-Sentra lifecycle, supported adapters and live checks](META_SENTRAS.md)
- [Overall Sentra architecture](SENTRAS.md)
- [Scaling design](SENTRA_SCALE.md)

Verification commands:

```sh
python -m pytest -q
python -m app.meta_sentry_check --live
```

The first is deterministic application validation. The second makes bounded external reads and can fail independently. Deployment validation additionally requires the actual configured service, its source canaries and reviewable real evidence.
