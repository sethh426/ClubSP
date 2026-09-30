# Release evidence

## Seller conversations and internal training — 2026-09-30

Delivered: property-linked contacts, role/permission evidence, manually recorded
conversations with retry-safe UUIDs, explicit/phrase-based stop detection,
matching-email suppression and draft cancellation, confirmed/hypothesis seller
profiles and latest-contact pain-point counts. Local template replies retain
conversation/profile/deal/financial context, invalidate after changes and require
owner review. Twelve synthetic lessons and owner-assessed practice persist with
curriculum version, six ratings, hard failures and review notes.

Validation: 68 Python tests and four desktop/mobile browser workflows passed
locally. Tests cover concurrent message retries, cross-property suppression,
preserved review history, stale draft rejection, latest-profile counts, untrusted
conversation content, self-assessment retry behavior, HTTP routes and browser
reload persistence. GitHub Actions must pass before merge. All example
conversations are synthetic.

Storage upgrade: contact/event/suppression/message/profile/draft/practice tables
and indexes are added without replacing property or money records. Back up a
stopped database before upgrading. Earlier releases retain but do not display
these records or apply their suppression controls.

Limits: no mailbox integration, external sends, background sequence or complete
language classifier. Templates/curriculum remain internal drafts. Permission
records are not legal campaign clearance. Scores are owner self-assessments,
not AI evaluations or proof that the system can close profitable transactions.

## Official parcel evidence — 2026-09-30

Delivered: one exact Allen County, Indiana iMap parcel lookup, fixed provider
registry, twenty-attempt daily budget, twenty-four-hour cache, ten-second timeout,
response-size/redirect limits, persisted failures, normalized identity comparison
and acceptance/rejection. Accepted records retain provenance and superseded facts;
unknown fields are not filled with guesses. No credentials or paid calls.

Validation: 57 Python tests and two desktop/mobile browser workflows passed
locally. Browser tests inject an explicitly synthetic adapter; no positive real
owner record is claimed. Official service metadata and the configured adapter's
live no-match response were checked. A real parcel's identity and original source
must still be reviewed by the owner. GitHub Actions must pass before merge.

Storage upgrade: a research-snapshot table and property/request-day indexes are
added without replacing existing evidence. Older code retains but does not show
the new snapshots. Back up a stopped database before upgrading.

Limits: exact parcel keys only; no lead discovery, comps, appraisals, contact
permission, title clearance or autonomous acceptance. The provider may be slow
or unavailable; failures remain visible. The general Update Knowledge workflow
and email transport are still pending.

## Operations and money — 2026-09-30

Delivered: proposed seller terms, owner cash limits, fixed-price assignment/resale
forecasts, integer-cent cash/escrow ledger, retry-safe writes, auditable reversals,
completion/loss reconciliation and current-only portfolio contribution totals.
The 18 Operations catalog, seeded/custom tasks, exceptions, deadlines and review
records are now visible in the browser. Contract stage gates require current
financial assumptions and completed owner reviews.

Validation: 45 Python tests and two desktop/mobile browser workflows passed locally.
GitHub Actions is also required before merge. Synthetic test records are isolated
in the ignored browser-test database; there are no seeded live opportunities.

Storage upgrade: existing property/evidence databases keep their records; new
finance/task tables and indexes are added automatically. Back up a stopped
workspace before upgrading. Downgrading preserves the new tables but older code
will not show or enforce the money/task gates.

Limits: owner-entered figures and evidence references are not bank, title or funds
verification. Contribution is pre-tax; overhead is included only when entered.
No live research, email, e-sign, funds transfer or paid provider is activated by
this release. Task deadlines are shown locally; external reminders are pending.
