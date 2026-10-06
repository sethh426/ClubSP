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

## Temporal Intelligence Engine

Every successful dynamic acquisition now appends a source observation. Content
signatures ignore row/key ordering, compare field hashes for reviewed identities,
and record baseline, stable and changed samples. Events preserve prior/new
signatures, changed fields, newly sampled identities and identities absent from the
sample. Absence is not interpreted as deletion, ownership change or complete
coverage. Changes also invalidate older compiler cache even inside its age window.
Mapping or activation changes start a new baseline.

Enabled per-source policies acquire from the server's existing scheduler. Source
polling starts at a reviewed base interval, halves on changes and expands by 50%
after three spaced stable observations. It maintains an EWMA of observed
inter-change gaps and clamps every interval between a reviewed minimum (at least
one hour) and the approved source freshness target (at most seven days). This is
learning detection timing from samples, not knowing when a publisher changed data.
Rapid repeated observations do not teach a slower cadence. Budget ceilings can
prevent a desired freshness interval; the saved state explicitly reports that gap.

SQLite per-source leases and unique reservations prevent duplicate scheduled
acquisitions across processes. Next due times, cadence, events, errors and rolling
24-hour call/cost limits persist across restarts. Calls are reserved before network
work, including failed/interrupted attempts. Disabled, re-quarantined or differently
activated sources are excluded. Background data acquisition requires an explicit
reviewed per-source policy; discovery automation never enables it.

- `GET /api/sentras/temporal`: recent observations, series and policies.
- `POST /api/sentras/temporal/policy`: active `sentra_id`, `owner_reviewed=true`,
  `note`, optional `enabled`, `min_interval_seconds`, `base_interval_seconds`,
  `daily_call_limit`, `daily_cost_limit_cents`, `cost_cents`.
- `POST /api/sentras/temporal/cycle`: bounded due-source checks, optional
  `max_sources` (default 3, maximum 10). The usual scheduler calls this every wake.

The Sentra Intelligence workspace exposes policies, observed change counts,
cadence and event details. Tests cover ordered history, reordering invariance,
adaptive bounds, budget/lease contention, disabled sources, failed reservations,
cache invalidation and restart persistence. The disposable live smoke verifies
an actual due-source acquisition and zero repeated acquisitions after restart.
