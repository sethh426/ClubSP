# Funding Desk

Open `/funding` from the property workspace. Record current underwriting and a
financial plan before adding a partner or lender review. This complements the
opportunity queue; it does not source property, recommend investments, approve
a loan, authorize a contract, send messages, or move money.

## Records

Each review records one funding counterparty and the aggregate amount earmarked
for this particular deal. Record the external requirement with a basis, commitment
amount, your cash requirement, additional contingent liability or guarantees,
known financing/partner costs, review/expiry dates, and unresolved conditions.
The amounts are stored as integer cents. Missing funding remains unknown.

An owner-reviewed status requires explicit confirmation plus references for terms,
deal-specific allocation, repayment/recourse obligations, and costs reconciled
with underwriting. An empty unresolved-condition list also requires a reference
showing conditions were resolved or none apply. References and acknowledgements
are entered by the owner; the app does not independently validate them.

The known-cost check compares the entered funding cost against recorded owner
transaction and partner payout allowances, plus the funding/holding allowance
for resale. Passing this bound does not prove correct allocation. The owner must
reconcile costs with the current underwriting without omitting or double-counting
fees, interest, or partner payouts. Principal is not profit or a financing fee.

The cash check conservatively uses the largest of planned cash exposure, recorded
unrecovered cash, or owner cash required plus additional contingent liability.
It does not independently quantify guarantee losses, draw schedules, recourse,
or all portfolio obligations. Review those with the funder and appropriate advisers.

## Readiness and history

Recorded checks remain blocked for pending/withdrawn or expired reviews,
shortfalls, unresolved conditions, excessive cash exposure, costs above relevant
allowances, and changed financial-plan/underwriting versions. Resale funding and
owner cash must cover at least the purchase price; repair, closing, holding and
other requirements still need a complete owner-reviewed funding basis.

Reusing the exact allocation evidence reference across active deals blocks both
reviews. Use deal-specific, earmarked allocation evidence. The app is not a bank
balance or shared credit-facility reservation system and does not independently
verify available capacity or multiple-counterparty funding arrangements.

Reviews must identify the financial-plan and underwriting versions shown to the
owner. A concurrent terms change rejects the save instead of silently attaching
old review evidence to new terms. Revisions append to history and must supersede the latest review. Request UUIDs
make identical retries return the original record; changed payloads cannot reuse
that key. Ended deals retain history and cannot receive new funding reviews.

Passing the recorded checks means only that these owner-entered records satisfy
these comparisons. Availability, actual disbursement, funds received, title,
signatures, and closing authority remain separate. Existing contract-stage gates
are unchanged. The Funding Desk is advisory and never clears an execution gate.

## API and checks

- `GET /api/funding`: current comparisons and immutable review history.
- `POST /api/deals/{id}/funding`: append a review with `request_key`; later reviews
  supply `supersedes_id` from the current version. Supply `financial_plan_id` and
  `underwriting_id` from the current Funding Desk response.
- Existing Host/Origin and JSON-size restrictions apply.
- An additive `funding_reviews` table lives in the existing SQLite workspace;
  existing database backups include it. Restoring an old backup loses later reviews.

Run `python -m pytest -q` and `npx playwright test -c playwright.funding.config.js`.
Tests use explicitly synthetic deals, funding evidence, dates, and money. No paid
provider, real counterparty, mailbox, or transaction is contacted.

## Deal action pipeline

The desk combines funding reviews with the current `opportunities` workspace snapshot when that feature is installed. It consumes the other module's live reasons and buyer counts without changing its policy, matching, or sourcing logic. Missing opportunity checks stay unavailable; stored buyer-match snapshots are not presented as current checks. Contracted and later deals continue through the recorded operation tasks.

Each card shows a next action, an expandable checklist, assigned open tasks and due dates, base/downside contribution, unrecovered cash, and current ledger reconciliation. Forecasts remain estimates and unreconciled results remain unknown. Ended deals leave the active shortlist and surface reconciliation work. No funding record automatically closes a task or advances a stage.

The shortlist includes at most five active deals, ordered by owner-review status, outside-buy-box status, and recorded downside contribution, with unknown downside values last within each group. Filters expose active, needs-action, owner-review, ended, and all deals. Owner-review candidacy is advisory and never authorizes execution. This view does not reserve a shared funding facility or rank unentered properties.
