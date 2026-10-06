# First 25 Sentra collectors

The first wave contains **25 fixed source collectors**. Meta-Sentras and
unconfigured Apify Actor families are excluded from this count. This extends
the Sentra foundation in PR #67; it does not enable unattended production runs.

## Collector roster

| # | Sentra ID | Output | Required input |
| --- | --- | --- | --- |
| 1 | `allen_county_accdc` | County availability notice observation | None |
| 2 | `allen_county_north_campus` | Advertised sale terms, parcel references and bid window | None |
| 3 | `allen_county_sheriff_sales` | Current/upcoming sheriff PDF sale notices | None |
| 4 | `allen_county_imap_parcel` | Exact parcel, reported owner and site-address evidence | `parcel_key` |
| 5 | `rentcast_sale_listings` | Bounded active sale listings in a buyer-demand market | `search_intent` |
| 6 | `realestateapi_inventory_preflight` | Property Search count; no property results | `search_intent` |
| 7 | `allen_county_tax_sale` | Tax-sale page and document-link observations | None |
| 8 | `allen_county_assessor_resources` | Assessor resource page and links | None |
| 9 | `allen_county_treasurer_resources` | Treasurer resource page and links | None |
| 10 | `allen_county_recorder_resources` | Recorder resource page and links | None |
| 11 | `allen_county_building_resources` | Building-permit resource page and links | None |
| 12 | `allen_county_planning_hearings` | Planning-hearing document index | None |
| 13 | `allen_county_zoning_resources` | Zoning-map resource page and links | None |
| 14 | `fort_wayne_code_resources` | Code-compliance resource page and links | None |
| 15 | `fort_wayne_redevelopment` | Redevelopment notice/agenda resource page | None |
| 16 | `indiana_surplus_property` | State surplus real-estate notice index | None |
| 17 | `hud_home_resources` | Official HUD home-sale resource page and links | None |
| 18 | `gsa_property_disposition` | Federal property-disposition resource page and links | None |
| 19 | `census_address_geocoder` | One supplied address match and range-based coordinates | `address` |
| 20 | `census_county_housing` | 2024 ACS county housing, vacancy, rent/value estimates and margins of error | `state_fips`, `county_fips` |
| 21 | `rentcast_property_record` | One address-matched property record with selected tax/sale history | `address` |
| 22 | `rentcast_rental_listings` | Bounded active rental listings | `search_intent`; optional `max_monthly_rent` |
| 23 | `rentcast_value_estimate` | Provider AVM value and range for an address-matched subject | `address` |
| 24 | `rentcast_rent_estimate` | Provider long-term rent estimate and range | `address` |
| 25 | `rentcast_market_statistics` | ZIP sale/rental listing statistics | `zip_code` |

Resource monitors do not retrieve individual permit, tax-balance, violation,
deed or zoning records. They preserve page changes and useful links, with
`fetched:false` on links. Linked sources need a separate collector/review before
their content can be treated as evidence. Government information pages do not
become listings automatically. The sheriff collector is the exception: it
follows up to three current/upcoming, same-host PDF links on its fixed index.
Its annual index URL needs review at the year boundary.

## Execution and evidence

- `GET /api/sentras` lists exactly this wave, required inputs, readiness,
  health, freshness and a compact last-run summary. It makes no external calls.
- `POST /api/sentras/run` executes one fixed collector with
  `confirm_external_request:true`; owner authentication and matching Origin
  are inherited from the application.
- `GET /api/sentras/runs/{run_id}` returns saved evidence and provenance.
- The operator CLI provides the same list, run and evidence operations.

Every successful run stores a versioned evidence envelope, unreviewed records,
payload digest, per-response byte hashes and source URLs, bounded query
parameters, schema fingerprint, timestamps and execution duration. No API key
is stored in the result. Provider contact fields are excluded. Empty results
are explicit and do not masquerade as schema drift.

Change detection compares the **same input query** with its last successful run.
A different parcel or market establishes a separate baseline. Record hashes
identify additions/removals, and shape changes are flagged for review; this is
observation change detection, not proof of a completed sale or ownership change.
Accepted property facts, deals and outreach remain in the existing review flows.

## Budgets, health and configuration

Runs reserve request quota in SQLite before external execution. The reservation
survives failures and process interruption. Calls to one source are serialized;
an expired three-minute lease can recover without clearing its quota. An
idempotency key cannot be reused for different inputs. Runs without an explicit
idempotency key reuse fresh successful evidence unless force refresh is selected.

Each source permits at most 20 reserved HTTP requests per Indianapolis business
day. Ordinary collectors reserve one; the sheriff collector reserves four.
Execution streams at most 2 MB across all responses, follows no HTTP redirects,
and limits acquisition to 45 seconds overall with 10-second per-request timeouts.
PDFs are additionally limited to ten pages per document. No source auto-retries.
After three failures a source cools down for one hour and retains its last good
evidence. A normalizer rejecting required fields counts as a failure. Successful
execution proves transport/normalization, not independently verified facts.

`RENTCAST_API_KEY` configures six collectors (5 and 21–25).
`REALESTATEAPI_API_KEY` configures collector 6. Both use the documented
`X-Api-Key` header, never query-string secrets. The existing provider flows and
all new collectors share their respective monthly caps:

| Setting | Default |
| --- | --- |
| `CLUBSP_RENTCAST_MONTHLY_REQUEST_CAP` | 40 requests/month |
| `CLUBSP_REALESTATEAPI_MONTHLY_COUNT_CAP` | 20 requests/month |
| `CLUBSP_SENTRAS_DISABLED` | Off; set `1` to stop the whole wave |
| `CLUBSP_DISABLED_SENTRAS` | Empty; comma-separated source IDs to stop |

Request caps are local usage ceilings, not a dollar-spend guarantee; actual
provider billing remains plan-dependent. Missing credentials and invalid inputs
use no quota. Configuration can come from the process environment or the
allowlisted `.env` fields; a file never overrides an existing environment value.
Public source status is not inferred from the registry's active/standby label:
new databases begin with every collector's health **unverified**.

## Operator commands

Use the same `--db` path as the running application. The default matches
`python -m app.server`: `data/clubsp.sqlite3`.

```bash
python -m app.sentra_cli --db data/clubsp.sqlite3 list
python -m app.sentra_cli --db data/clubsp.sqlite3 run allen_county_tax_sale
python -m app.sentra_cli --db data/clubsp.sqlite3 run census_county_housing --input '{"state_fips":"18","county_fips":"003"}'
python -m app.sentra_cli --db data/clubsp.sqlite3 run rentcast_sale_listings --input '{"search_intent":{"market":"Fort Wayne, IN","property_types":["single_family"],"max_total_price":150000},"max_results":10}'
python -m app.sentra_cli --db data/clubsp.sqlite3 evidence RUN_ID_FROM_RESPONSE
```

`run` is an explicit source request. It exits nonzero on failure. Lookup
collectors need an actual supplied parcel/address; no fabricated property key
is selected automatically. A purchase-price ceiling is never substituted for
monthly rent; rental listings support `max_monthly_rent` separately.

## Validation and remaining activation work

All 25 collectors have synthetic transport tests that exercise execution,
normalization, persisted provenance and the absence of automatic deal creation.
The sheriff fixture is a real PDF generated from synthetic content. Additional
tests cover exact/ambiguous identity, response caps, redirects, challenges,
owner/Origin enforcement, idempotency, caches, shared provider budgets,
concurrent reservations, restart recovery, preserved evidence and missing-data
sentinels. Meta-Sentra tests exercise discovery through proposal/review and
failure recording; a failed HTTP probe stays quarantined, and dynamic activation
cannot replace a fixed collector ID.

The first-25 rollout is integrated with the latest executable Meta-Sentra
foundation (`35a66a4`). The combined application suite passes **605 tests**.

On October 6, 2026, direct canaries for 17 public collectors could not reach
external sources from the restricted build workspace. They produced transport
failures and **zero live verifications**. The exact-parcel collector was not
canaried without a selected property key. Seven provider collectors were
credential-blocked. Fixtures are not live verification.

Before enabling continuous operation, run the canaries on the actual server,
configure the two provider credentials and quotas, review source access/use
requirements, and inspect positive identity/estimate samples. Continuous
scheduling for these fixed collectors and automatic downstream intake remain
separate rollout work and are not claimed complete by this wave. The existing
Meta-Sentra foundation supplies its own governed dynamic-source execution and
discovery/health scheduler; this rollout preserves those controls.

## Primary interface references

- [Allen County tax sale](https://www.allencounty.in.gov/270/Tax-Sale)
- [Allen County assessor](https://www.allencounty.in.gov/164/Assessor)
- [Allen County building department](https://www.allencounty.in.gov/234/Building-Department)
- [Allen County hearing documents](https://www.allencounty.in.gov/1255/Public-Hearing-Documents)
- [Indiana surplus real estate](https://www.in.gov/idoa/state-resource-management/state-and-federal-surplus/real-estate-sales/surplus-property-information/)
- [GSA real property disposition](https://www.gsa.gov/real-estate/real-property-disposition)
- [Census geocoding API](https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html)
- [2024 ACS API](https://api.census.gov/data/2024/acs/acs5/examples.html)
- [RentCast API](https://developers.rentcast.io/reference/introduction)
- [RealEstateAPI Property Search](https://developer.realestateapi.com/reference/property-search-api)
