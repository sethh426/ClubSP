# Demand-First Property Provider Integration

ClubSP should not become a generic property-data browser. External providers are
inputs to the Commitment Graph. Standing buyer demand defines the search; provider
records are staged as evidence candidates; owner review decides what becomes part
of a property file.

## Research conclusion

### Pilot provider: RentCast

RentCast is the best first live pilot for the current Commitment Graph because its
sale-listing endpoint directly supports the filters ClubSP already knows how to
derive from standing buyer mandates:

- city/state or ZIP;
- property type;
- price ranges;
- bedrooms and bathrooms;
- square footage;
- year built;
- active/inactive listing status;
- pagination up to 500 results.

Its current developer documentation also states a 20 requests/second key limit and
a free Developer plan with 50 included API requests per month. Importantly, the
provider does not offer a hard overage cutoff, so ClubSP must enforce its own local
request budget before network calls.

The first adapter therefore defaults to **40 attempted requests/month**, configurable
with `CLUBSP_RENTCAST_MONTHLY_REQUEST_CAP`. This is deliberately conservative and
can be raised by an operator with a paid plan.

### Inventory preflight: RealEstateAPI

ClubSP now uses RealEstateAPI only for an optional **count-mode preflight** before
record retrieval. PropertySearch count mode can measure how much inventory matches a
buyer-derived query without importing candidate records. The provider documents count
mode as 0 credits on paid plans and 1 credit on pay-as-you-go plans, so ClubSP still
requires explicit confirmation and enforces a local request cap.

RealEstateAPI is also attractive for a later full-record adapter because PropertySearch
supports advanced property/investor filters such as absentee owner, auction,
foreclosure, REO, vacant, free-and-clear, high-equity, cash-buyer and investor-buyer
flags. Those can become useful sourcing dimensions after ClubSP proves the first
buyer-demand/listing loop.

Those fields should not be added to the Commitment Graph merely because a provider
offers them. Add them only when there is a buyer/business reason to search them and
when their semantics are normalized and tested.

### Secondary evidence provider: ATTOM

ATTOM is a good candidate for deeper assessor/transaction/property evidence. Its
developer examples expose property snapshot/detail, identifier/APN data, AVM
packages and sale snapshots. ClubSP should treat AVM outputs as provider estimates,
not truth, and retain existing evidence/underwriting review boundaries.

## Architecture rules

1. **Demand compiles the query.** A provider never defines ClubSP's buy box.
2. **Every external request is explicit.** No background provider calls in this phase.
3. **Every request is cost-capped locally before the network call.**
4. **No automatic retries.** A retry can consume provider quota and must be deliberate.
5. **No automatic deal creation.** Returned candidates enter the existing pending
   sourcing-review queue.
6. **Provider credentials stay in environment variables**, never in SQLite, browser
   state, source URLs or Git.
7. **No API key in URLs.** RentCast authentication is sent in the `X-Api-Key` header.
8. **Redirects are disabled** for live provider calls.
9. **Responses are bounded** and schema-normalized before storage.
10. **Provenance survives normalization.** Search-run ID, source URL, response digest,
    provider record ID and per-record digest are retained.
11. **Sensitive/unneeded provider fields are not copied.** A property listing contact,
    phone number or other unnecessary personal field is not imported just because it
    exists in the upstream response.
12. **Provider records are discovery evidence.** They do not prove seller authority,
    title, condition, repair cost, financing or contact permission.

## Current implementation

`app/provider_integrations.py` now provides:

- provider registry and operator-visible capability metadata;
- capability-based routing from each buyer-derived search intent;
- RentCast search-intent compilation for market, property type, price, beds, baths,
  square footage, and year-built ranges;
- strict market parsing and bounded listing normalization;
- explicit per-call confirmation;
- local monthly usage cap;
- six-hour reuse of identical successful searches unless an operator explicitly
  forces a refresh, so repeated buyer demand does not waste API quota;
- pooled-demand lineage: identical queries from multiple buyer mandates share one
  provider request while every contributing buyer/mandate remains linked to the run;
- optional HTTPX live transport (`pip install .[integrations]`);
- no automatic retries;
- 2 MB response cap;
- provider search audit rows and response digests;
- idempotent sourcing batches keyed by response digest;
- staging into the existing pending candidate-review workflow;
- provider quality telemetry: requests, staged candidates, review acceptance,
  downstream deals, and recorded closes.
- RealEstateAPI count-mode inventory preflight with its own local monthly cap and 24-hour cache;
- RentCast total-match capture through `includeTotalCount`, so ClubSP can compare staged records with the broader matching inventory.

Environment variables:

- `RENTCAST_API_KEY`
- `CLUBSP_RENTCAST_MONTHLY_REQUEST_CAP` (default `40`)
- `REALESTATEAPI_API_KEY`
- `CLUBSP_REALESTATEAPI_MONTHLY_COUNT_CAP` (default `20`)

The browser sees only whether the credential is configured and the remaining local
request budget. It never receives the credential value.

## Next improvements

1. Add a provider response contract layer with Pydantic when live credentials are
   connected and the first real payloads can be tested.
2. Add a second adapter only after the first provider's normalized candidate flow is
   proven with real outcomes.
3. Add zero-result and stale-listing quality metrics so provider routing can learn
   which source/query patterns are productive without treating small samples as predictions.
4. Add queue workers only when repeated scheduled searches are authorized; preserve
   the same local budget ledger and never let a queue bypass it.
5. Keep provider search explainable: every result should be able to say which buyer
   mandate generated the search and which criteria it satisfied.