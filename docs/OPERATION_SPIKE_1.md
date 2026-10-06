# Operation Spike #1

## Evidence Compiler

The compiler searches all subsets of up to ten reviewed source profiles to select
an exact lowest-cost eligible evidence sequence. It accounts for dependency
capabilities, per-source cost ceilings, latency tie breaks, freshness, source
activation versions, and an operator policy threshold. It replans after each
acquisition and stops when the requested capabilities have sufficient matching
records. Empty responses and records for a different parcel do not provide coverage.

Only public dynamic Sentras that have completed Meta-Sentra probe, proposal,
operator approval and activation can be used. A profile additionally records a
reviewed identity field, capability-to-field mappings, dependencies, estimated cost
ceiling, policy weight and review note. Profiles retain immutable review versions.
The runtime never substitutes discovery metadata for evidence or executes paid
Actors. A policy weight is a completeness/quality heuristic, not a measured
probability of truth. Repeated or duplicate sources cannot accumulate confidence;
the compiler uses the strongest weight per capability, and conflicting normalized
values require review.

Acquisitions preserve bounded raw payloads, normalized matching claims, content
hashes, source URL, retrieval time, latency, activation and profile versions.
These snapshots are staged research evidence and do not import property facts,
create deals, match/verify funding, contact anyone or authorize transactions.
Evidence remains scoped to an exact county and record identity. ArcGIS execution
currently reads up to 25 rows; identity outside that bounded sample is reported as
missing. Full-source search and source-specific filtering require dedicated adapters.
Source retrieval time is not proof of publication recency.

`/sentras` offers plan previews, bounded collection, saved run details and mapping
review. Operator APIs:

- `GET /api/sentras/evidence`: profiles and last 50 runs.
- `POST /api/sentras/evidence/profile`: active `sentra_id`, `owner_reviewed=true`,
  `identity_field`, `field_map`, optional `requires`, `confidence`, `cost_cents`,
  `latency_ms`, required `note`.
- `POST /api/sentras/evidence/plan`: `subject`, `jurisdiction`, `capabilities`,
  optional `threshold` (0.5), `max_cost_cents` (0), `max_calls` (5),
  `max_age_hours` (24). Preview makes no external requests.
- `POST /api/sentras/evidence/run`: same request plus UUID `request_key`.

A durable run and per-call cost reservation precede network work. Repeating the
same request key returns its saved result; different inputs reject reuse. A live
concurrent request returns `running`; expired interrupted runs require a new key
and never replay unconfirmed network work. Failed calls retain reserved cost. These
are research budget estimates rather than charges, refunds, or financial ledgers.
Cached snapshots survive restarts and expire at the smaller of requested max age
and the source freshness target. Changed approval/mapping versions invalidate reuse.

Validation: exact bundle-vs-singleton costs, dependencies/cycles, budgets, early
stop, empty/foreign records, failures, stale cache, version changes, conflicts,
restart persistence, idempotency and desktop/mobile UI. `python -m app.evidence_check`
checks a real public layer in a disposable database, including restart cache reuse;
it makes no production source approvals or property imports.
