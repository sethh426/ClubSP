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

The minimum bid is compared with the current recorded seller-price limit; this
is not a valuation, profitable-deal score, funding verification or endorsement.
The ACCDC adapter recognizes an explicit empty-inventory notice. Other layouts
require review. More sources need separate adapters and documented use terms.

API: GET /api/discovery; POST /api/discovery/check with {"source_id":"accdc"}
or {"source_id":"north_campus"}. No background research is enabled.
