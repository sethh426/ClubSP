# Implementation Roadmap

## Honest current status

Implemented: local property/evidence workspace, SQLite persistence, outcome learning,
manual deal creation and gated stages, assignment/resale scenario underwriting
(downside/base/upside), buyer criteria records and explainable matching, Python
unit/HTTP/concurrency coverage, and desktop/mobile browser workflows.

Partially implemented: the deal core has no task/exception queue, cash-at-risk ledger,
buyer packet, signed-document storage, contract/closing workflow, or actual-versus-
forecast profit reconciliation. Buyer funding status and evidence are owner-entered;
the app does not independently verify funds, authority, or intent to close.

Not implemented: live property sourcing/comps, email integrations, suggested replies,
training UI, researched knowledge refresh, MCP transport, public hosting, or
transaction execution. The Operations documentation remains a target build
specification for those capabilities.

The Operations documentation is a build specification, not a feature activation.

## Build sequence and completion criteria

| Release | Build | Acceptance evidence |
| --- | --- | --- |
| 1 — Deal operating core | Deal/owner/buyer records, stages, evidence gaps, task/exception ownership, underwriting cost ledger, scenario engine, profit reconciliation | One entered property follows a complete simulated assignment and resale; costs reconcile; unknown costs stay unknown; ceiling/exposure violations block commitments |
| 2 — Real research | Provider registry, one chosen county/property source, licensed comps, normalized identities, provenance, bounded budgets | Small real sample checked against original records; collisions/conflicts surfaced; rights/costs recorded; no fabricated comps or secret paid calls |
| 3 — Buyer demand | Verified buy boxes, dates, funding status, buyer matching and packets | Matches explain criteria; stale buyers flagged; seller-private information protected; interest distinguished from funds |
| 4 — Communication | Dedicated mailbox adapter, durable outbox/inbound sync, eligible campaigns, opt-out/suppression, grounded reply panel | Sandbox sends/replies work; duplicate callbacks/timeouts safe; reply/stop cancels pending sends; no commitments hidden in drafts |
| 5 — Knowledge and training | Update Knowledge UI/job pipeline, sourced items/diffs/review/rollback, reviewed objection library, synthetic role-play and evaluations | Button-only research; partial failure honest; legal changes staged; no prompt-injection escape; role-play evaluated without pretending models trained |
| 6 — Transaction coordination | Reviewed document/e-sign handoff, deadlines, title conditions, authorized offer workflow, buyer exit, settlement evidence | Version/terms match; signatures and received money are separate; approvals and deadline exceptions persist; no fabricated title clearance |
| 7 — Reliable private deployment | Authentication, secrets, encrypted transport/backups, restoration, persistent queue, costs/audit/owner controls | Recover after restart; backups restore; privileges tested; stop controls stop external jobs |
| 8 — Live pilot | One jurisdiction and selected strategy, reviewed policy, real counterparties and controlled volume | Evidence to underwriting to approved terms to professional closing to actual reconciliation; record failed deals as well as wins |

These are dependency gates, not promised completion dates.
Reliability, evidence and privacy controls are built throughout, not postponed
until release 7. Remote deployment requires its security gate first.

## Current next release

The Deal + Underwriting + Buyer foundation is now implemented in the local app.
It should answer:
what do we know; what is missing; who can buy; what can Seth offer; how much
cash is at risk; what could go wrong; and what would net profit be?

Current deliverables: property-linked deal stages, evidence-gated contract/closing
milestones, manual assignment/resale scenarios, and buyer criteria matching. The
owner still makes all decisions; the calculator is not a valuation and does not
make or transmit offers.

Next, finish cash-at-risk and actual-versus-forecast ledgers, then add one verified
property research source. Keep outreach automation behind a separate compliance
gate until property evidence and deal economics are dependable.

## Example test fixture, not a market recommendation

Use synthetic property and counterparty records. Configure a $145,000 buyer
ceiling, $125,000 seller price, $20,000 fee and $4,000 own costs. Verify $16,000
pre-tax contribution, plus the separate $2,000 planning reserve scenario.
Test a changed buyer ceiling, increased repairs, failed funding and missing title.
No hardcoded claim that these values correspond to a real opportunity.

## Release evidence

Every release records code/version, reviewed scope, test results, source/provider
configuration, costs, migrations/backups, limits, known gaps and rollback.
Do not label Setup Required features active or use mock data without labeling it.

## Owner setup needed before live external actions

Market/jurisdiction; assignment or funded resale strategy; cash/risk/spend limits;
approved sender/entity/role and mailbox; source licenses/credentials; professionals;
reviewed channel/disclosure/contract policies; verified buyers; standing
authorization scope. Development and sandbox tests can proceed without these
being filled. Live commitments cannot.

## Business scorecard

Qualified properties -> useful conversations -> qualified seller fit -> acceptable
underwriting -> executed rights -> executable buyer -> completed closing ->
received funds -> net contribution -> business net after unsuccessful acquisition
and fixed costs.

Measure costs, elapsed time, failure reasons, evidence quality and owner effort
at every step. Activity volume alone does not establish that the app makes money.
