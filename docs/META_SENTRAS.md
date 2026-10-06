# Meta-Sentra operations

Meta-Sentras discover and supervise source collectors. Catalog metadata, schema
samples and execution results are kept outside the property-fact and deal-action
stores. A source's usefulness score determines review priority, not truth.

## Operational lifecycle

1. Discovery searches Apify Store, ArcGIS Online, Data.gov v4, or an explicitly
   scoped CKAN catalog. Responses are streamed within a 2 MB budget. Bad provider
   contracts and transport failures are recorded for both manual and scheduled runs.
2. Candidates are deduplicated by identity and endpoint and persisted in quarantine.
   Rediscovery preserves approvals and reviewed capabilities. Catalog landing
   pages are not substituted for underlying dataset resources.
3. Probing reads at most 256 KiB from a public HTTPS endpoint. Failed, redirected,
   oversized, malformed or unsupported samples cannot advance the lifecycle.
   ArcGIS service roots produce quarantined candidates for declared layers; layer
   identifiers are read from service metadata rather than guessed.
4. A successful schema probe records field names/types, shape, sample digest,
   latency, identity/freshness field hints, ETag and Last-Modified. A proposal
   requires a successful structured probe and no outstanding usefulness/access blockers.
5. An operator explicitly approves the proposal with a durable review note.
   Activation binds the source to a unique registry ID, its reviewed schema,
   jurisdiction, rights note, supported executor and freshness target.
6. The dynamic registry is executable after restarts. Every execution verifies
   the active registration and approved field contract before returning a
   provenance-rich result. Request data cannot override the approved URL.
7. Health checks rotate across active dynamic sources and record failures and
   field/type drift. Broken sources are removed from the executable registry,
   preserving prior evidence. Re-activation requires a new probe, proposal and
   approval; an old approval cannot restore a broken source.

Public requests reject credentials in URLs, nonstandard HTTPS ports, private
addresses and DNS results that point to local/private infrastructure. Each request
pins a validated address while retaining the original TLS hostname. Redirects,
environment proxies and unbounded/compressed responses are disabled.

## Provider and executor support

| Provider/source | Current behavior |
| --- | --- |
| ArcGIS public catalog | Search metadata; probe declared layers; acquire up to 25 rows from approved layers. |
| Apify Store | Discover and quarantine Actor recommendations. Paid Actor execution is not enabled by this lifecycle. An Actor cannot be activated as a generic JSON source. |
| Data.gov v4 | Uses the current GSA API with `DATAGOV_API_KEY`; selects DCAT distributions. Missing credentials are visible and manual failures are persisted. |
| CKAN | Searches an explicit public `catalog_url`; namespaces dataset IDs by catalog and prefers JSON/CSV resource endpoints. |
| Public JSON | `official_api` or `direct_http`, using the approved endpoint and bounded field contract. |
| Public CSV | `direct_http` or `file_parser`, validating headers and row structure. |
| HTML, browser extraction, webhooks, credentialed/provider-specific sources | Require a dedicated adapter. Unsupported modes are refused rather than registered as active. |

Data.gov documentation identifies the v4 endpoint and requires a personal key for
automated queries: https://resources.data.gov/catalog-api/
The scheduler never substitutes `DEMO_KEY` for a production key.

## Scheduler

Health monitoring starts with the normal server by default. New-source automatic
discovery is opt-in. Jobs run on a background thread, retain due times and errors in
SQLite, and use durable leases to prevent duplicate work across processes.

```bash
# Enable bounded discovery. Omit Data.gov until its personal key is configured.
CLUBSP_META_AUTODISCOVERY=1
CLUBSP_META_DISCOVERY_INTERVAL_SECONDS=21600
CLUBSP_META_DISCOVERY_MAX_QUERIES=6

# Health monitoring defaults to enabled, every hour, at most 10 sources per cycle.
CLUBSP_META_HEALTH_MONITORING=1
CLUBSP_META_HEALTH_INTERVAL_SECONDS=3600
CLUBSP_META_HEALTH_MAX_SOURCES=10
```

Intervals cannot be shorter than an hour. Discovery permits 1–12 queries per
cycle and health permits 1–50 sources. Size the health budget to the active
registry and freshness targets; these limits do not promise real-time monitoring
of thousands of sources. Source publication timestamps are retained as hints,
not proof that upstream facts are current.

Only public ArcGIS/Apify catalogs run when a Data.gov key is absent. The state API
shows provider readiness. Discovery and health automation never call approval or
activation APIs.

## Owner API

All mutation routes require a matching Origin. Owner authentication applies when
enabled; production continues to require owner authentication. Existing source
review and downstream action controls remain in force.

| Request | Function |
| --- | --- |
| `GET /api/sentras/meta?limit=100&offset=0` | Paginated quarantine and dynamic registry; global counts, recent runs, provider readiness and scheduler state. |
| `POST /api/sentras/meta/cycle` | Bounded demand/market-coverage discovery cycle. |
| `POST /api/sentras/meta/discover` | Search a catalog; use its returned cursor for the next page. |
| `POST /api/sentras/meta/probe` | Probe a quarantined source or discover an ArcGIS root's declared layers. |
| `POST /api/sentras/meta/propose` | Stage a successfully probed, eligible source. |
| `POST /api/sentras/meta/review` | Explicit operator approval/rejection with a note. |
| `POST /api/sentras/meta/activate` | Activate an approved source with a supported executor. |
| `POST /api/sentras/meta/execute` | Acquire an approved source using only its `sentra_id`. |
| `POST /api/sentras/meta/health` | Check up to `max_sources` active sources. |
| `POST /api/sentras/meta/history` | Read lifecycle events and recent probe/health/execution checks for a fingerprint. |
| `POST /api/sentras/meta/requarantine` | Suspend an active source with a recorded reason. |

The Meta-Sentra schema upgrades explicitly from v1 to v2. Older activations without
a supported executor and structured schema are re-quarantined for fresh review.

## Verification and the Operation Spike gate

```bash
python -m pytest -q
python -m app.meta_sentry_check --live
```

The smoke command uses a disposable database, searches public catalogs and probes
an ArcGIS layer. It never activates production sources or executes paid Actors.
PR CI includes the same live provider check.

A passing test suite proves the controlled lifecycle; it does not prove the
user's hosted process is running this version, has outbound access/credentials,
or has the scheduler enabled. Check the actual hosted owner API and source
history after deployment before declaring production operational.

Operation Spike #1 remains gated on that operational verification. Its three
additions are Evidence Compiler, Temporal Intelligence Engine and Shadow
Intelligence Network.
