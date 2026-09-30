# Implementation Roadmap

## Honest current status

Implemented: local property/evidence workspace, SQLite persistence, outcome learning,
manual deal creation and gated stages, assignment/resale scenario underwriting
(downside/base/upside), buyer criteria records and explainable matching, Python
unit/HTTP/concurrency coverage, and desktop/mobile browser workflows.

Implemented in the Operations/money release: proposed terms and owner cash limits,
fixed-price downside comparisons, integer-cent cash/escrow ledger, retry-safe
entries and append-only reversals, completion/loss reconciliation, current-only
portfolio contribution totals, 18 Operations catalog, seeded/custom tasks,
exceptions, owner/deadline assignments and evidence-gated stage reviews.

Implemented in the parcel research release: an exact Allen County official
parcel-key lookup, fixed provider registry, normalized identity comparison,
pending acceptance/rejection, source provenance, historical fact supersession,
request budgets, caching and recorded failures. Service metadata and a live
no-match protocol were checked; positive records use synthetic test fixtures
until a real owner-reviewed parcel is entered.

Implemented in the conversation/training release: property-linked contacts,
role/permission evidence, manual conversation history, retry-safe messages,
matching-email suppression and draft cancellation, confirmed/hypothesis seller
profiles, latest-contact pain-point counts, local template reply drafts with
context invalidation and immutable owner review, twelve synthetic lessons and
persisted six-dimension owner self-assessment.

Partially implemented: property research, communication CRM, buyer packets, independent funding
checks, licensed comps, professional title review and document workflows. Tasks
are local records; external email reminders and workers are not connected.

Not implemented: autonomous property sourcing/comps, email integrations, AI-generated
replies/automatic grading, researched knowledge refresh, MCP transport, public hosting, or
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

Next, add the button-triggered knowledge source/review workflow and licensed
market evidence. Keep outreach
automation behind a separate compliance gate until research and economics are
dependable. Knowledge refresh remains button-triggered and reviewable.

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
