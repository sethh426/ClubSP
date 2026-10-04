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

### Secondary provider: RealEstateAPI

RealEstateAPI is attractive for a later adapter because PropertySearch supports
advanced property/investor filters such as absentee owner, auction, foreclosure,
REO, vacant, free-and-clear, high-equity, cash-buyer and investor-buyer flags.
Those can become useful sourcing dimensions after ClubSP proves the first
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
- RentCast search-intent compiler;
- strict market parsing;
- property-type mapping;
- explicit per-call confirmation;
- local monthly usage cap;
- optional HTTPX live transport (`pip install .[integrations]`);
- no automatic retries;
- 2 MB response cap;
- RentCast listing schema normalization;
- provider search audit rows;
- idempotent sourcing batches keyed by response digest;
- staging into the existing pending candidate-review workflow.

Environment variables:

- `RENTCAST_API_KEY`
- `CLUBSP_RENTCAST_MONTHLY_REQUEST_CAP` (default `40`)

The browser sees only whether the credential is configured and the remaining local
request budget. It never receives the credential value.

## Next improvements

1. Add normalized optional mandate filters for beds/baths/square-footage/year-built.
2. Add a provider response contract layer with Pydantic when live credentials are
   connected and the first real payloads can be tested.
3. Add a second adapter only after the first provider's normalized candidate flow is
   proven with real outcomes.
4. Add provider health metrics: successful/failed searches, zero-result rate,
   candidate-review acceptance rate and closed-deal lineage by provider.
5. Add queue workers only when repeated scheduled searches are authorized; preserve
   the same local budget ledger and never let a queue bypass it.
6. Keep provider search explainable: every result should be able to say which buyer
   mandate generated the search and which criteria it satisfied.