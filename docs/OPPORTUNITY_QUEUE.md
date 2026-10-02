# Evidence-backed opportunity queue

This release assesses **entered pre-contract deals**. It does not discover leads,
fetch new evidence, generate valuations, send messages, make offers, commit money,
or activate an operating mandate. No paid providers or AI calls are introduced.

## Setup and decisions

Save an explicit buy box: exact city/state markets, strategies, property types,
seller-price limit, peak deal/portfolio cash limits, minimum downside contribution,
evidence review interval and owner rationale. No market or budget is assumed.
Policy versions are append-only; the latest version applies to the whole queue.
The minimum downside contribution is currently nonnegative; accepting a deliberate
modeled loss requires a future separately reviewed risk-policy extension.

Each `/api/state` read recalculates the queue in a single database read session:

- `outside_buy_box`: recorded market/type/strategy/price does not fit.
- `blocked`: current facts conflict, financial-plan blockers apply, or modeled
  cash/downside exceeds the queue policy.
- `research`: required evidence, current underwriting, plan, buyer funding review,
  or pre-contract task reviews are missing.
- `owner_review`: no listed queue blockers remain; owner must still verify
  assumptions, deal-specific buyer interest, professional reviews and actual terms.

Order is review, research, blocked, outside; within each group, current criteria-fit
buyer count then address/ID. This is an explainable triage order, not a calibrated
profit score, probability, economic forecast or instruction to transact.

## Evidence and buyer comparison

Only active property facts not superseded by another fact are current. Attribute
spaces/case are normalized; `property type` and `property_type` share one group.
Conflicting values remain unresolved; no confidence score or SQL row order silently
chooses a winner. Fact confidence is entered evidence metadata, not calibration.
Owner-of-record evidence does not establish signing authority or contact permission.

New underwritings store the digest and IDs of current facts. Changed evidence or a
legacy underwriting without the digest requires explicit owner review and resaving.
The [CSV sourcing workflow](SOURCING.md) also snapshots accepted comparable sales
for each subject. Missing sales, changed evidence and sales older than a year
prompt review. This is a subject-level sale set, not per-assumption repair/quote
dependencies. Comp/repair/funding inputs remain manual. The UI labels the fixed-price scenarios
pre-tax and unverified; closed sales, inspection scopes and professional reviews
still need to substantiate them. Stale/future fact dates and stale underwriting
prompt research. Review intervals are internal policy, not legal deadlines.
Fact observation time is not the publication/effective date of an underlying
document. Re-entering an old source today does not independently make it fresh;
source-date validation remains part of the owner evidence review.

Buyer comparisons use current property-type evidence and invalidate eligibility
when underwriting evidence changes/is untracked. Funding review retains the
existing 30-day owner-marked rule. A criteria-fit buyer is not a deal-specific
commitment, independent proof of funds or permission to share seller information.
Queue reads do not insert match runs; explicit Run buyer matching still records one.

## Cash and execution boundaries

Portfolio exposure sums the greater of planned peak and recorded unrecovered cash
for active deals, plus unrecovered cash on ended deals. Missing active plans are
unknown, never zero-risk. This conservative planning sum is not bank availability,
a dated cash-flow model, or complete signed-obligation accounting.

Contracted/disposition/closing/ended deals are excluded from candidate triage but
their relevant cash exposure still counts. Existing operating tasks remain their
management surface. Queue decisions are **advisory**: this release does not change
existing contract-stage authority, freeze executed terms, or enforce portfolio
policy on every write. Those controls remain required before external automation.
The sale-evidence workflow additionally requires a current underwriting evidence
digest before moving to contracted; see its documented limits on valuation review.

## API and verification

`POST /api/opportunities/policy` accepts `markets[]`, `strategies[]`,
`property_types[]`, `max_seller_price`, `max_deal_cash_at_risk`,
`max_portfolio_cash_at_risk`, `min_downside_net`, `evidence_max_age_days`, and `basis`.
It is protected by the existing Host/Origin/JSON/body-size checks.
`GET /api/state` includes `opportunities` with the current policy, decision reasons,
current fact IDs, live buyer comparisons, fixed-price economics and exposure gaps.
`execution_authorized` is always false.

Synthetic unit/HTTP tests cover conflicts, supersession, stale/future dates,
evidence invalidation, legacy snapshots, downside limits, portfolio unknowns,
lost-deal cash, policy validation/versioning and read-only behavior. Browser tests
cover policy save/reload, safe text rendering and opening the selected deal on
desktop/mobile. No real owner outreach or paid data is used in tests.
