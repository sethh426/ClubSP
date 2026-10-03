# Official sale-notice discovery

The official-notice panel checks two fixed Allen County pages: ACCDC availability
and the North Campus sale notice. This is a first bounded discovery adapter, not
a general listing feed or a claim that either source has a suitable property.

Checks require the button, a matching Origin, and a configured source ID. Each
source is limited to six attempts per UTC day, a 24-hour successful-check cache,
a ten-second timeout and 512 KiB response. Redirects are rejected. No user URL,
login, payment, bid, contact, or property/deal creation is accepted by the adapter.
Provider errors and changed formats are recorded without invented candidates.

Stored checks retain URL, time, content hash and a short relevant excerpt. Raw
HTML is not executed or stored as a full page. The North Campus parser extracts
the advertised address, parcel IDs, minimum bid and Eastern-time bid window.
Parcel portions require survey review. An advertised window does not prove that
the county has not sold or withdrawn the property. Cached records become stale;
expired advertised periods are flagged even before another network check.

The minimum bid is screened against each active buyer’s recorded price ceiling.
These preliminary checks do not verify full costs, interest, funding or profitability.
The ACCDC adapter recognizes an explicit empty-inventory notice. Other layouts
require review. More sources need separate adapters and documented use terms.

API: GET /api/discovery; POST /api/discovery/check with {"source_id":"accdc"}
or {"source_id":"north_campus"}. No background research is enabled.

## Notice-to-intake handoff

A saved notice can be staged into the existing pending candidate intake workflow.
The server reads the candidate from the saved check; request-supplied addresses,
prices or source URLs are rejected. The check must be the latest for its source,
less than 24 hours old and within the advertised bid window. No personal purchase budget or financial policy is required for lead research.
Buyer criteria are informational and do not gate staging or identity acceptance.
The owner supplies a verified ZIP, selects a named parcel and records identity
and surveyed-portion review notes. All notice parcels, terms and source evidence
remain attached; a selected parcel does not establish whole-parcel availability.

Staging creates one pending intake, not a property or deal. Repeated submissions
reuse the intake. Rechecking unchanged terms can refresh a pending intake after
review; conflicting identity fields require review of the existing intake. The
existing acceptance step independently rechecks freshness and latest notice identity. Exclusion remains available even when acceptance is blocked.
An advertised minimum bid is retained as notice evidence and is never converted
into negotiated seller terms or a financial plan.

POST /api/discovery/intake requires a matching Origin and exactly check_id,
candidate_index, parcel_id, zip, property_type, reviewer, note and
identity_confirmed=true. No paid API, messaging or background checks are added.

## Buyer-oriented lead research

ClubSP does not assume the researcher purchases the property. Active buyer
profiles are screened on the notice market and advertised minimum bid, with
property-type screening when a reviewed type is supplied. No profile means
unknown buyer fit, not a research block. Known mismatches are explained.
Full acquisition costs, fees, repairs, strategy, buyer interest and current
funding still require review. A profile marked verified is an owner-recorded
status, not confirmation of a current buyer commitment. Fee arrangements and
transaction model remain undecided; this feature authorizes no outreach,
agreement, contract assignment, offer or purchase. Existing pre-contract deal
financial controls remain unchanged.
