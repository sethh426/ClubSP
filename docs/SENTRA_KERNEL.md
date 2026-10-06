# Sentra kernel and registry

The kernel provides one validated definition for built-in and explicitly approved dynamic Sentras. Registry snapshots belong to one workspace. Activating a source does not modify the process-wide catalog or another database.

Definitions include status, declared capabilities, separately verified capabilities, acquisition mode, source type, geographic coverage, source URL, provenance references, source schema fingerprint, recorded rights review, freshness target, credential variable name, estimated cost class, per-run cost ceiling, and daily execution request allowance. Credential values are never part of a definition.

## Runtime interfaces

| Interface | Behavior |
| --- | --- |
| `GET /api/sentras?limit=100&offset=0` | Paginated canonical definitions, execution blockers, observed run health, registry revision, and total counts. Limit is 1–200. |
| `POST /api/sentras/route` | Read-only capability/market routing. Input: `capability`, optional `market`, optional boolean `verified_only`. Declared routing does not claim verified coverage. |
| `POST /api/sentras/run` | Bounded observation of an approved source. Input: `sentra_id`, `request_key`, optional `operation` (`observe` or `refresh`), optional `max_bytes` (up to 262144). |
| `POST /api/sentras/meta/execute` | Compatibility interface for approved dynamic sources; now uses kernel execution reservations. New callers should use `/run` with a stable request key. |
| `POST /api/sentras/meta/health` | Existing bounded health/drift monitor. It can re-quarantine a source; it cannot approve one. |

These interfaces use the existing owner session. POST requests require a matching Origin. This is an HTTP application interface; an MCP transport is section 9.

Example observation:

```json
{
  "sentra_id": "reviewed_county_source",
  "request_key": "county-check-20261006-001",
  "operation": "observe"
}
```

The Sentra ID must already exist through discovery, successful schema probing, proposal, explicit review and supported activation. The example is a placeholder, not a registered live source.

## Execution guarantees

- Canonical dynamic configuration is written in the same transaction as activation, with references to the persisted approval and schema. Legacy activations without a kernel contract remain blocked from kernel execution until re-reviewed.
- A run reserves its allowance before any external fetch. Concurrent duplicate keys share one run. Reusing a key with different inputs or a changed definition is rejected.
- Reservations, the complete immutable definition snapshot, results and errors survive restart. Failed attempts consume their reservation. A still-running entry remains unresolved after restart and is never silently resubmitted.
- Allowances use UTC calendar days. ArcGIS execution reserves two requests for layer verification and bounded records; the other supported public-source observations reserve one. The reservations are conservative if an attempt fails before all requests occur.
- Only the supported uncredentialed public-source path with a recorded zero-cost ceiling can run through the kernel. Metered provider accounts and Actors require their own durable cost accounting gate. A request payload cannot grant Actor approval or override a source URL, credential or cost policy.
- The shared transport pins public DNS answers for the request, preserves TLS server-name verification, rejects redirects and compressed responses, bounds streaming bodies, and limits time. The existing Meta-Sentra JSON, CSV and ArcGIS schema checks remain in use.
- A source/schema/activation change during an observation withholds the result. Re-quarantine removes the source from active routing while prior checks, run snapshots and approval events remain available for audit.
- Replaying a completed request preserves the original observation time. It does not renew freshness. Health distinguishes never-run, in-progress, healthy, stale, unavailable and withheld runs.

The daily execution reservation does not yet provide a unified allowance across discovery, probes, health jobs and all provider families. Durable worker queues, lease recovery, retries, dead letters, retention and aggregate spending controls are section 8 gates.

## Evidence and geographic boundaries

Every result remains `raw_source` evidence. No run automatically adds a property fact, creates a deal or initiates contact. A successful fetch and an approved schema do not establish parcel identity, title, current availability, a comparable sale or buyer acceptance.

Coverage uses explicit reviewed market aliases. A country's provider description does not prove coverage in a particular city or county. `verified_capabilities` must be a subset of declared capabilities; new raw-source activations receive no verified business capabilities. Discovery gaps therefore remain visible until normalized and reviewed evidence establishes coverage.

Built-in catalog status remains configuration metadata. An `active` label alone does not mean the generic kernel adapter can run that source, that credentials are present, or that a production canary has passed. The catalog exposes these execution blockers explicitly.

## Verification

The Python suite exercises complete synthetic lifecycle-to-execution handoff, workspace isolation, routing across 3000 definitions, concurrent duplicates, restart replay, failed-attempt budgets, retained interrupted runs, schema drift, in-flight source invalidation, owner authentication and matching Origin. Existing Meta-Sentra transport, CSV/ArcGIS, catalog and durable scheduler tests remain included.

Live source validation is separate. Run the existing bounded command on a host with source access:

```sh
python -m app.meta_sentry_check --live
```

It never activates sources or imports facts. Success establishes catalog/schema reachability on that host, not complete production property extraction. An actual source's approved observation and evidence handoff still need a host-specific canary before its rollout gate is closed.
