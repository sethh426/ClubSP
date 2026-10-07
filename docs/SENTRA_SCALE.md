# Sentra real-time scale design

## Goal

ClubSP must support thousands of independently governed sources without coupling source acquisition to normalization, matching, or downstream actions.

## First production topology

1. **Scheduler/router** decides which Sentra is due based on freshness, buyer demand, source cost, health and jurisdiction.
2. **Executor workers** run the declared acquisition mode.
3. **Source event stream** receives compact provenance-rich execution events.
4. **Normalizer workers** convert source-specific payloads into evidence contracts.
5. **Identity workers** resolve parcel/address/event identity and deduplicate cross-source records.
6. **Health workers** track failure rate, latency, schema drift and stale sources.
7. **Buyer-match workers** compare normalized evidence against standing mandates.
8. **Opportunity queue** receives explainable, evidence-linked research candidates.

## Transport

Initial production recommendation: Redis Streams with consumer groups.

Reasons:
- persistent ordered event log;
- acknowledgement and pending-entry tracking;
- replay after worker failure;
- horizontal worker groups;
- retention/trimming controls;
- already aligned with ClubSP's optional Redis scale dependency.

The application contract must not depend on Redis-specific IDs. Every event carries its own UUID/idempotency key, Sentra ID, source digest, schema version and timestamp. This keeps a later Kafka/Redpanda migration mechanical.

## Stream families

- `sentra.execute` — due execution jobs.
- `sentra.observed` — successful raw observations.
- `sentra.failed` — bounded execution failures.
- `sentra.normalized` — normalized evidence records.
- `sentra.changed` — meaningful diffs from prior source state.
- `sentra.health` — health/schema-drift telemetry.
- `sentra.match` — buyer-demand matches.
- `sentra.deadletter` — events requiring operator review.

## Backpressure

Workers must consume at bounded concurrency. A source with repeated failures is cooled down rather than retried aggressively. Expensive/metered sources use separate budgets and lower concurrency. Browser sources must never share the same concurrency ceiling as inexpensive HTTP/API sources.

## Partition/sharding strategy

Shard first by acquisition mode, then by jurisdiction/source family when volume requires it. Examples:

- HTTP/API workers
- Apify orchestration workers
- browser workers
- document/PDF workers
- county-public-record workers
- commercial-provider workers

A Sentra may be reassigned without changing its ID or evidence semantics.

## Near-real-time semantics

"Real time" means source-aware freshness, not blindly polling every source every second.

- webhook/feed sources: ingest immediately;
- fast-changing auctions/listings: minutes where permitted/economical;
- county notices: source-appropriate intervals;
- static regulatory/reference pages: hours/days;
- expensive APIs: demand-triggered or change-sensitive.

The router should schedule by expected information value per unit cost and source-change rate.

## Apify

Use Apify Actors as elastic remote executors when they are the best acquisition mechanism. Prefer completion/failure webhooks over repeatedly polling run status. Actor discovery is separate from actor approval. ClubSP records approved Actor ID/version, expected output contract, max charge, timeout and source rights before production execution.

## Scaling checkpoints

At 100 Sentras: measure correctness and provenance.
At 250: worker sharding + stream lag dashboard.
At 500: automatic health/schema drift.
At 1,000: adaptive scheduling based on source change rate and buyer demand.
At thousands: dynamic source discovery/onboarding with approval gates, regional worker pools, and automatic replacement of low-value/broken sources.
