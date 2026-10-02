# Candidate intake and comparable-sale evidence

This local workflow imports real user-supplied exports; it does not discover live leads or connect to a paid data API. Open **Candidate intake & comparable-sale evidence**, choose a type, record the source URL, source as-of date, use-rights basis and confirmed city/state, then paste or load a CSV. Previewing stages rows without creating properties, deals, contacts or messages.

## Source contracts

| Import type | Required exact CSV headers | Review result |
| --- | --- | --- |
| Candidates | `address,zip,parcel_id,property_type` | New property with reviewed parcel/type facts; no owner claim, motivation claim or contact permission |
| County completed sales | `Parcel Number,Address,Sale Date,Sale Price,Living Area` | Evidence attached to a selected subject property; no new lead |

The [Allen County sales viewer](https://acimap.us/comps/) offers sales searches and CSV downloads. Its FAQ says exports cover the rows shown, so narrow the search or split results; do not treat an export as a complete county dataset. This importer supports the displayed header names and optional `Property Class`/`Class`. It does not call an undocumented county bulk endpoint or independently certify the file as official. Other export formats require explicit mapping before import.

The displayed export also includes `Acreage`, `Neighborhood Code`, `Property Code`, `Year Built`, `Bath`, `Price/SqFt`, `Land Value`, `Improvement Value` and `Total Value`. The current release retains those cells in each row's raw source record for auditability, while normalizing the core sale fields used by review and underwriting. It intentionally does not turn those optional fields into valuation inputs automatically; the reviewer must decide which fields are relevant and record that basis.

Use one confirmed city per batch. The county export has no required city/state columns; the app does not assume that every Allen County address is in Fort Wayne. Keep parcel IDs as strings, including leading zeros. Sale dates must be ISO `YYYY-MM-DD` or `MM/DD/YYYY`; source dates are ISO. Sale dates cannot follow the source date or lie in the future. Prices must be positive finite amounts with at most two decimals; living area must be positive. Currency symbols and thousands commas are supported. Missing or zero prices/areas stay invalid rather than receiving defaults. Blank ZIP/type/parcel on candidate rows is invalid.

## Boundaries and provenance

- Maximum 50 data rows and 45,000 UTF-8 bytes per batch; HTTP requests retain the existing 65,536-byte JSON limit. Split exports that exceed either bound.
- The source metadata and content hash form an idempotency key. Exact retries, including concurrent retries, return the existing batch.
- Raw parsed cells, source metadata, hash, row errors and review references remain stored. The CSV hash represents the trimmed, BOM-normalized input; the original byte-for-byte file is not retained.
- Invalid rows cannot be accepted. Repeated parcel/sale identities in one batch stay invalid. Existing candidate addresses or recorded parcel identities are rejected conservatively; resolve identity in the existing property file rather than silently merging.
- Acceptance requires reviewer, notes, evidence reference and explicit identity confirmation. Exclusion also records reviewer and rationale. Reviews cannot be overwritten. Accepted candidate facts retain the source as-of date rather than becoming fresh because of import time; confidence is a fixed conservative 0.5, not a measured reliability score.
- Sales require explicit owner confirmation of sale validity, arm's-length status and comparability. The app cannot verify this checkbox. Review condition, market, property class, size, sale concessions and other differences in the notes. A subject cannot be its own comparable. A repeated accepted parcel/sale for the same subject is rejected; withdraw the old evidence and import a corrected export to replace it.
- Withdrawal keeps the original acceptance and records withdrawal separately. A previously accepted row is not reused for a different subject; submit another reviewed batch with its own source basis when necessary.

## Underwriting dependency

## Research opportunity discovery

Accepted candidate-property rows are ranked in `state.discovery` against the latest
buy-box policy. The score is an evidence-completeness and policy-fit triage score,
not a probability of profit. Market fit contributes 40 points, property-type fit
25, owner evidence 15, parcel identity 10 and a current source date 10. Candidates
outside the buy box and properties already in an active deal are labeled separately.
Missing seller price/terms always remains a reason for research; the discovery
queue never invents a price, seller motivation, valuation or expected return.

Discovery only uses reviewed candidate imports. It does not scrape the viewer,
contact owners, create deals, spend money or authorize an offer. After a candidate
is opened, the owner must verify identity, ownership/authority, condition, seller
terms and comparable evidence before underwriting.

Saving underwriting captures all currently accepted sales for that subject, including source and review metadata, and a deterministic digest. It preserves a historical snapshot if the evidence is later withdrawn. The entered exit price remains manual: there is no automatic average, price adjustment, appraisal or guarantee of saleability.

The advisory opportunity queue requests comparable evidence when none exists, asks for a refresh when any accepted sale is older than 365 days, and invalidates underwriting when the accepted evidence changes or a legacy version did not track it. Buyer comparisons also reject changed/untracked sale evidence. Moving to contracted requires a matching current sale-evidence digest; it does not require a minimum comp count or independently validated valuation. Legacy underwriting must be reviewed and resaved. After resaving underwriting, refresh the financial plan as usual.

These checks do not replace valuation review, seller authority, title/contract review, signed buyer commitments or the existing operation tasks. The next integration is an authorized provider feed with a documented license, market coverage, budget, retry policy and the same staged review boundary. No provider credentials, payments, scraping, scheduling, outreach or offer execution are introduced here.

## Local API

- `POST /api/sourcing/import`: `kind`, `provider`, `source_url`, `source_date`, `rights_basis`, `city`, `state`, `csv`.
- `POST /api/sourcing/rows/{id}/review`: `action` (`accept`/`exclude`), `reviewer`, `note`, `evidence_reference`; acceptance also requires boolean `identity_confirmed`; sales require `property_id` and boolean `sale_verified`.
- `POST /api/sourcing/sales/{id}/withdraw`: `reviewer`, `note`, `evidence_reference`.
- `GET /api/state`: `sourcing.batches`, `sourcing.rows`, `sourcing.sales`; statuses and review histories are included.
