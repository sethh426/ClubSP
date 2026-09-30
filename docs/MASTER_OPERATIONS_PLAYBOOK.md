# ClubSP Master Operations Playbook
Version 1.0 · September 30, 2026 · Owner: Seth Pina

## Mission

ClubSP is Seth's personal property acquisition, wholesaling, and resale system.
Success means completed transactions and money retained after all costs, failed
deal expenses, and agreed partner payouts. It is not a software sales business.

The existing app is a working local workspace: properties, manual evidence,
estimates, outcomes, and SQLite persistence. This document specifies the complete
target operating system. Describing an Operation does not mean it has been coded.
IMPLEMENTATION_ROADMAP.md identifies what exists and what must be built.

Assignment and purchase/resale are different transaction paths. An assignment
transfers permitted contractual rights; a resale requires acquisition and resale
completion and funding. Each deal records its actual structure, rights, funding,
obligations, and professionally reviewed jurisdiction policy. Do not imply
property ownership when only contract rights are held.

## Autopilot contract

| Level | Actions | Execution |
| --- | --- | --- |
| Automatic internal work | Research within enabled sources/budgets, analysis, classification, drafts, matching, reminders | Event-driven or user-triggered |
| Authorized external automation | Eligible emails using activated sequences/templates, routine scheduling and acknowledgments | Recorded standing authorization; eligibility rechecked before every send |
| Owner commitments | Offers, material counteroffers, signatures, deposits, funding, purchases, assignments, payments | Approval of the specific terms/version/amount, or a separately reviewed explicit mandate |

Standing authorizations specify operation, geography, channel, recipient rules,
template version, frequency, spend ceiling, expiry, and exception conditions.
They are not unrestricted authority. This document does not activate outreach.

Knowledge research runs only when Seth presses **Update Knowledge**. Incoming
messages, delivery receipts, job completion, deadlines, and closing events still
update normal operational state. Neither a stale indicator nor a failed research
job silently launches another research run.

Every Operation produces an audit event with inputs, evidence, policy/model/tool
versions, costs, outputs, decision, responsible person, and next action. A blocked
deal does not halt unrelated safe internal work.

## Operation map

| Operation | Name | Output |
| --- | --- | --- |
| 01 | Command and economics | Buy box, budget, authority, exposure limits |
| 02 | Knowledge and research | Button-driven sourced updates and version diffs |
| 03 | Market selection | Dated local acquisition thesis |
| 04 | Buyer demand | Verified buyer criteria and exit evidence |
| 05 | Property sourcing | Traceable candidate queue |
| 06 | Evidence and diligence | Reconciled facts and material gaps |
| 07 | Qualification and pain points | Seller-confirmed needs and deal fit |
| 08 | Communication | Controlled emails, inbox, follow-up, suppression |
| 09 | Discovery and replies | Grounded suggested replies and next questions |
| 10 | Underwriting | Scenarios, cost responsibility, price ceilings |
| 11 | Sales training | Role-play, objection library, evaluated coaching |
| 12 | Negotiation and offers | Approved terms and recorded revisions |
| 13 | Contracts and title | Executed rights, disclosures, deadlines |
| 14 | Disposition | Qualified buyer and executable exit |
| 15 | Closing | Verified transaction completion and disbursement |
| 16 | Profit reconciliation | Actual deal and campaign economics |
| 17 | Learning | Evaluated improvements from wins and losses |
| 18 | Reliability and control | Recoverable jobs and owner command center |

## Operation 01 — Command and economics

**Goal:** define what ClubSP may pursue before it spends or commits.

**Inputs:** target geography, property types, strategy, available cash, funding
options, minimum net target, downside tolerance, owner time, and professionals.

**App work:** create BusinessProfile, BuyBox, BudgetPolicy, and AuthorizationPolicy.
Separate cash from borrowing capacity and anticipated proceeds. Show outstanding
deposits, signed obligations, due-diligence deadlines, and monthly operating spend.

**Owner decisions:** select the initial market and strategy, spending limits,
cash reserve, maximum simultaneous deals, earnest-money exposure, and who can
approve changed terms. Fort Wayne/Allen County is a candidate, not a preset mandate.
Unset limits block related spending/commitment automation.

**Exceptions:** insufficient funding, unclear authority, unavailable closing
support, or a strategy dependent on unverified buyers.

**Exit:** a written buy box and reviewed operating mandate.
**Metrics:** available/committed cash, expenses, owner hours, exposure, realized net.
**Expect:** start with one market and strategy, then expand after measured results.

## Operation 02 — Knowledge and research

**Goal:** researched business knowledge that changes on command.

**Domains:** acquisitions, seller discovery, negotiation, buyer/disposition sales,
comps and repair scopes, local markets, funding, economics, contracts, title,
closing, legal compliance, channel/provider policies, privacy/security, and
lessons from actual transactions.

**Button:** choose All Domains or selected domains, jurisdiction, sources, scope,
and cost ceiling. Run one resumable research job; show progress, cancel, partial
failures, changed items, citations, and affected Operations.

**Pipeline:** fetch permitted primary sources -> capture dates/jurisdiction/hash
-> deduplicate -> extract bounded claims -> compare to active version -> identify
conflicts/applicability -> stage changes -> evaluate -> publish within policy
-> preserve rollback. Compliance, contract, and financial execution changes
require appropriate review before becoming live policy.

**Knowledge states:** draft, active, superseded, disputed, stale, review required.
Last successful verification and latest attempt are separate. Inaccessible does
not mean unchanged. An enacted rule, proposed rule, effective requirement, court
decision, explanatory article, and provider policy must be distinguishable.

**Freshness:** configurable internal targets, not legal deadlines. Proposed
starting targets: market context reviewed within 30 days; transaction-critical
assumptions checked before offers; applicable compliance coverage checked before
campaign activation/commitments. Stale dependencies warn or block their specific
action and prompt a button update, without automatic research.

**Security:** researched content is data, never permission to send messages,
change rules, reveal credentials, or execute instructions found in a webpage.

**Exit:** versioned update, per-domain coverage, evidence, diff, reviewers, rollback.
**Metrics:** unresolved conflicts, stale dependencies, coverage, costs, citations.
Detailed specifications: KNOWLEDGE_AND_AUTOMATION_SPEC.md.

## Operation 03 — Market selection

**Goal:** select a segment where acquisition economics and real buyers coexist.

**Inputs:** licensed closed sales/listings where available, inventory, observed
selling times, price changes, property type/condition, local buyer activity,
funding quotes, and public records.

**App work:** create dated MarketSnapshots by segment; separate closed sales from
asking prices; disclose sample size/coverage; compare periods; explain the thesis
and counterarguments. Regional indexes and mortgage series are context, not a
property valuation.

**Decisions:** owner approves the target segment and exclusions. Evaluate
economically relevant property facts rather than protected traits or demographic
proxies. HUD's Fair Housing overview is a foundational compliance source. [S5]

**Exceptions:** unsuitable comps, thin coverage, optimistic exit assumptions,
or absent buyer demand.
**Exit:** documented market thesis, exclusions, review date.
**Metrics:** useful comp coverage, buyer demand, observed transaction speed,
predicted versus realized exits.

## Operation 04 — Buyer demand

**Goal:** establish credible exits before taking avoidable exposure.

**Inputs:** buyer-provided buy box, locations, property types, rehab tolerance,
price range, funding evidence, closing timeline, preferences, relevant history.

**App work:** separate interested, criteria-confirmed, funding-reviewed,
terms-agreed, deposit-confirmed, and previously-closed statuses. Date every
verification; match objective criteria; draft qualification questions.

**Decisions:** owner verifies identity, purchasing intent, and evidence through
appropriate channels. A purchased cash-buyer list does not prove current demand;
a proof-of-funds image does not independently establish available funds.

**Exceptions:** stale evidence, inconsistent terms, identity/funding mismatch.
**Exit:** buyer demand map and a credible exit path, without a guaranteed sale.
**Metrics:** verified buyers, written terms, withdrawals, close performance.

## Operation 05 — Property sourcing

**Goal:** relevant candidates with traceable provenance and controlled cost.

**Routes:** licensed APIs, permitted county assessor/recorder/tax/permit sources,
authorized listing data, seller submissions, owner entries, legitimate referrals,
and permitted research/extraction.

**App work:** normalize address/parcel/jurisdiction, deduplicate, preserve source
dates and conflicts, apply the buy box, route providers by coverage/license/cost,
cache permitted results, meter calls, and create the next research action.
Use a suitable direct API first; use Apify/browser extraction only where permitted.

**Prioritization:** property fit, price plausibility, evidence quality, buyer fit,
and seller-confirmed timing. Vacancy, tax, condition, and probate signals may be
incorrect; they do not establish motivation or contact permission.

**Decisions:** activate sources/campaigns within explicit budgets.
**Exceptions:** duplicate/mismatched parcel, restricted use, missing coverage,
rate limits, expensive low-quality enrichment.
**Exit:** candidate record, source, limitations, next task.
**Metrics:** unique eligible candidates, qualified cost, data errors,
source-to-close economics.

## Operation 06 — Evidence and diligence

**Goal:** surface material unknowns before commitments.

**File:** property identity, recorded ownership, seller authority, occupancy,
tenancies where relevant, condition/access, tax/payoff information, known liens,
permits, insurance/flood concerns, buyer constraints, professional title review.

**App work:** retain conflicting facts, attribute each value to evidence, separate
identity confidence/freshness/source reliability, and assign EvidenceGap tasks.

**Distinctions:** assessed value is not market value; record ownership is not
necessarily signing authority; preliminary searches are not title clearance;
an AVM is not an inspection; visible condition is not a complete contractor scope.

**Decisions:** owner/professionals verify authority, condition, costs, and title.
Unknown identity, authority, material condition, or transferability blocks
dependent commitments rather than being hidden inside a score.

**Exit:** sufficient coverage for the next decision with assigned unresolved items.
**Metrics:** critical gaps, conflicts, repair variance, title exceptions.

## Operation 07 — Qualification and pain points

**Goal:** discover the seller's actual goals and whether the transaction fits.

**Fields:** reason for considering selling, desired outcome, timeline, condition,
decision makers, occupancy, alternatives, communication preference, and proceeds
requirements if voluntarily provided.

**PainPoint record:** category, seller statement/evidence reference, date,
confirmed/hypothesis/disputed status, desired solution, next question.
Unknown is the default. Do not infer desperation, protected traits, or willingness
to accept less from a name, location, demographic data, or public record.

**Categories:** timing coordination, maintenance burden, net proceeds, process
uncertainty, reliability, privacy/showings, tenant/vacancy logistics, and ownership
administration if the seller raises it.

**Research:** Zillow's 2025 study identifies proceeds/timing and financing friction
as useful discovery topics. Its surveyed recent primary-residence sellers are not
a representative sample of all distressed/off-market owners. Research informs
questions; it does not label a particular person. [S1]

**Exit choices:** qualified, needs evidence, permitted nurture, unsuitable,
wrong person, suppressed—with a factual reason.
**Metrics:** confirmed needs, fit, reasons lost, preferences respected, actual closes.

## Operation 08 — Communication

**Goal:** accurate automated emails, reliable inbox processing, immediate stops.

**Prerequisites:** configured identity/mailbox, provider permission for the use
case, reviewed channel policy, recipient eligibility, suppression list,
activated templates/cadence, and authorization/budget.

**Outbound:** eligibility -> draft/grounding -> approval or standing mandate
-> queue -> eligibility recheck -> send -> reconcile delivery/reply. Stable
idempotency keys prevent duplicate messages. An uncertain send is reconciled
before retry, not automatically resent.

**Inbound:** authenticated mailbox/webhook -> deduplicate provider ID -> match
thread/contact -> classify -> pause scheduled follow-up -> propose next step.
Replies, opt-outs, bounces, and wrong-person reports cancel pending sequence work.

**Example test cadence:** day 0, day 4, day 10 then stop; configurable and not
a legal safe harbor. Apply recipient local time, frequency limits, and campaign
collision controls. Read receipts/open rates are not dependable business outcomes.

**Commercial email baseline:** truthful headers/subjects, applicable solicitation
identification, postal address, and opt-out. Covered opt-outs must be honored
within 10 business days under FTC guidance; ClubSP targets immediate suppression.
B2B commercial email is not categorically exempt. Determine applicability to
the actual acquisition messages before launching. [S2]

**Other channels:** separate consent/DNC/quiet-hours/recording/artificial-voice
policies; no blanket assumption that acquisition outreach or an email permission
clears calls/texts. Federal and state rules and provider terms must be applied
to the specific workflow. [S3, S4] Inbound parsing capability does not establish
a provider permits cold outreach. [S7]

**Exit:** legitimate next step or suppression.
**Metrics:** qualified replies, opt-outs, bounces, complaints, duplicates,
cost per useful conversation and completed deal.

## Operation 09 — Discovery and reply assistance

**Goal:** useful next replies grounded in the actual thread.

**Reply panel:** original message, confirmed facts/needs, stage, missing evidence,
intent, applicable policy, three drafts, supporting references, next question.

**Draft options:** concise, explanatory/empathetic, next-step. All share the same
verified facts. Suggest at most one or two relevant questions. Owner edits are
logged; private evidence stays in the internal pane unless appropriate to share.

**Automation:** approved low-risk acknowledgment/scheduling can run under policy.
Material price/term changes, uncertain legal claims, complaints, ambiguous
identity, wire changes, and money requests route to the owner/professional.

**Never invent:** cash/funding, ownership, buyers, inspections, rival offers,
deadlines, completed title work, or certainty of closing.

**Exit:** an eligible sent reply or draft with a named blocker.
**Metrics:** factual errors, owner edits, useful next steps, response latency,
complaints and actual outcomes.

## Operation 10 — Underwriting

**Goal:** determine whether a specific deal works after costs and downside.

**Inputs:** credible comps, as-is/after-repair ranges, repair scope/quotes,
buyer criteria, closing/title estimates, funding terms, holding duration,
deposits, transaction structure, costs and responsibilities.

**App work:** conservative/base/upside scenarios, explained comp selection,
cost ledger by Seth/seller/buyer/partner, sensitivity analysis, evidence gaps,
cash exposure, and a versioned offer ceiling. A generic 70% rule is insufficient.

**Assignment:** buyer acquisition ceiling follows the buyer's evidenced exit
economics minus buyer repairs, funding, holding, closing/selling, contingency,
and required margin. Maximum seller price = buyer ceiling minus planned assignment
fee. When starting from desired *net*, the fee must also cover Seth's costs,
agreed payouts, and contingency. Seth actual net = received fee minus own actual
costs minus agreed payouts.

**Resale:** net = resale proceeds minus purchase price, own acquisition/resale
closing costs, funding/holding/repair costs, acquisition/disposition expenses,
and agreed payouts. Financing proceeds/deposit returns are not profit.
Tax treatment is separate professional work.

**Illustration, not a real opportunity:** $145,000 buyer ceiling and $125,000
seller price could leave $20,000 gross fee; $4,000 own costs leave $16,000
pre-tax net. A $2,000 contingency allowance makes planned net $14,000 until
actual expenses are known. Do not double-count reserves as paid costs.

**Decisions:** owner approves ceiling, downside and funding. Material unknown
costs prevent offer-ready status.
**Exit:** reject, research, or approved offer recommendation.
**Metrics:** forecast/actual variance, exposure, planned and realized net.

## Operation 11 — Sales training

**Goal:** practiced, honest acquisition and disposition conversations.

**Modules:** accurate role disclosure, discovery/listening, qualification,
trade-offs/net proceeds, explaining evidence, objections, negotiation,
buyer funding, closing expectations, complaints and disengagement.

**Practice:** synthetic seller/buyer scenario -> role-play -> scored feedback
-> better response -> retry -> evaluated result. Grade listening, factual
accuracy, relevance, clear next step, economics discipline and policy.
Deception/policy failures fail regardless of persuasiveness.

**Objection method:** acknowledge -> clarify -> explain supported trade-offs
-> propose a real next step -> respect refusal.

| Objection | Response direction | Evidence/action |
| --- | --- | --- |
| Price too low | Ask whether amount or terms matter; explain assumptions | Check comps/condition, never fabricate repairs |
| Another buyer offered more | Respect their option; compare real conditions | Verify terms; no fake rival offers |
| Are you actually buying? | State approved truthful role and structure | Required reviewed disclosures |
| Don't trust investors | Ask concerns; offer independent document review | Identity and real title/closing process |
| Need family discussion | Allow time; identify decision makers | Authority and written summary |
| Need immediate cash | Do not promise immediate funds | Check feasible funding/title/timing |
| Don't want assignment | Respect the constraint | Fundable alternative or decline |
| Guarantee closing? | Explain remaining conditions honestly | Funding and closing evidence |
| Buyer margin too small | Compare actual numbers | Cost model and approved ceiling |
| Stop contacting me | Acknowledge and stop | Immediate suppression, no sales rebuttal |

**Internal training:** retrieval from reviewed knowledge and permissioned examples
first. LearningRecords are not model training. Future fine-tuning requires a
reviewed data-use basis, redaction, held-out evaluation, versioning and rollback.
Seller conversations remain private by default.

**Exit:** approved scenario/response library with reviewer and revision history.
**Metrics:** factual errors, useful next steps, respectful stops, owner edits,
real transaction results. See SALES_AND_COMMUNICATION_TRAINING.md.

## Operation 12 — Negotiation and offers

**Goal:** acceptable terms with disciplined economics.

**App work:** compare offers/terms, explain estimated seller net, capture revisions,
expiry, contingencies and counteroffers, prepare documents from approved inputs.

**Decisions:** owner authorizes specific offers and material changes to price,
deposit, conditions, dates or rights. AI phrasing must not silently alter terms.
Prices above ceiling are blocked; uncertain promises are removed.

**Exit:** rejection, permitted nurture, pending decision, or accepted terms ready
for proper execution. Verbal interest is not an executed contract.
**Metrics:** offer-to-execution, reasons lost, concessions, retained margin.

## Operation 13 — Contracts and title

**Goal:** correct rights, obligations, signatures, deadlines and title process.

**Inputs:** approved parties/authority/legal description/terms, reviewed
jurisdiction templates/disclosures, assignability, earnest money,
contingencies, access, closing professional.

**App work:** populate templates, detect deviation from approved terms, store
signature versions, track deadlines, create escrow/title handoff and exceptions.

**Decisions:** applicable law, disclosures, assignment rights, signing,
deposits, title clearance and funding remain owner/professional decisions.
Current Indiana text/applicability requires verification before activating
templates; the source register does not mark Indiana cleared.

**Exceptions:** missing authority/signatures, nonassignable terms, title issue,
funding gap, changed legal language, deadline risk. Prepare reviewed options;
do not assume an extension/cancellation is valid.

**Exit:** executed complete file, remaining conditions, confirmed closing plan.
**Metrics:** mismatches, missed deadlines, title exceptions, deposit exposure.

## Operation 14 — Disposition

**Goal:** qualified buyer and executable assignment/resale exit.

**App work:** match current buyer criteria; prepare a truthful packet with rights
offered, identity, condition, comps/assumptions, access, terms, title status,
and material limitations. Share only permitted information and legally
appropriate marketing/disclosures with eligible contacts.

**Distinctions:** interest is not accepted terms; proposed deposit is not escrow
receipt; funding image is not independent verification. Protect private seller
financial information.

**Decisions:** owner selects buyer, pricing, terms and documents; confirms rights
to market; maintains genuine backup options.
**Exceptions:** withdrawal, unverifiable funds, lack of access, no executable buyer.
Re-underwrite or pursue a lawful contractually available exit.
**Exit:** buyer agreement and verified remaining conditions/funding path.
**Metrics:** written terms, close rate, withdrawal, time, price erosion.

## Operation 15 — Closing

**Goal:** documented completion and verified disbursement.

**App work:** track documents/signatures/conditions, compare settlement costs to
signed terms and ledger, send authorized reminders, escalate missing work.
Applicable financed deals may use a Closing Disclosure; cash/assignment deals
can use other settlement records. Do not generalize loan-specific forms or
deadlines to every deal. [S8]

**State:** scheduled -> conditions pending -> ready per closing professional
-> signed -> funded/disbursed -> recorded/complete where applicable.
Only appropriate evidence advances each state. Signature is not cash receipt.

**Decisions:** professionals/owner verify title, authority, funds, signing,
settlement and release/recording. Independently confirm wire instructions using
a trusted known contact; email cannot automatically change payment details.

**Exceptions:** funding failure, wire change, settlement variance, missed date.
Assign an urgent task; recalculate economics and authorized options.
**Exit:** closing evidence, actual receipts/disbursements.
**Metrics:** completion, timing, cost variance, funds received.

## Operation 16 — Profit reconciliation

**Goal:** know what Seth actually earned.

**App work:** reconcile fees/proceeds, purchase/funding flows, paid costs,
reimbursements, partner payouts, and net; preserve forecasts; separate cash flow
and profit. Track deal contribution and business net including failed leads/deals,
research, communication, travel, inspections, legal/title, hosting and tools.

**Splits:** follow the real signed agreement, including gross/net basis and cost
responsibility. No presumed partner or percentage. Consistent cost allocation
prevents double counting. Display owner time and tax/reserve planning separately.

**Exit:** reconciled ledger with unresolved items clearly marked.
**Metrics:** actual net, cost per completed deal, failed-deal losses, cash
exposure, time to cash, owner hours, forecast error.

## Operation 17 — Learning

**Goal:** improve from evidence, including losses and rejected opportunities.

**App work:** compare predictions/outcomes; identify source errors, objections,
bottlenecks and costs; propose ranking/template/underwriting changes.
Keep sample size, period, geography, strategy, missing outcomes and uncertainty
visible. Small samples support hypotheses, not guarantees.

**Evaluation:** replay cases using only then-available facts; hold out tests;
check factual errors, policy, economics, buyer fit and net results. Prevent
outcome leakage. Association does not establish a sales technique caused a win.

**Promotion:** propose -> evaluate -> review -> limited release -> measure
-> rollback. Do not auto-rewrite rules or retrain because one deal succeeded.
**Exit:** evaluated change or explicit unproven hypothesis.
**Metrics:** forecast accuracy, errors, owner corrections, source profitability.

## Operation 18 — Reliability and control

**Goal:** unattended work that is observable, bounded and recoverable.

**Command center:** next actions, stage, draft replies, deadlines, deposits,
obligations, received funds, spend, stale research, failures and exceptions.
Every recommendation shows why it exists and what would invalidate it.

**Execution:** durable jobs, idempotency, retries/backoff/timeouts, webhook
verification/deduplication, dead-letter queue, restart/resume, audit history,
permission recheck before external action. No hidden infinite loops.

**Buttons:** Pause Outbound, Resume Authorized Outbound, Update Knowledge,
Run Property Research, Review Replies, Approve Offer, Record Outcome,
Export Deal File, Backup, Stop External Actions. Unbuilt integrations show
Setup Required, never fake success.

**Security:** authenticated private hosting before remote use, protected
credentials/backups, transport security, narrow sharing, attachment protections,
retention/deletion policies, no seller data/secrets in GitHub.

**Owner rhythm:** daily stored-event review and urgent decisions; weekly economics
and message quality review; button refresh when required. No research scheduler.

**Exit:** recoverable jobs, accountable exceptions, complete audit trail.
**Metrics:** recovery, duplicate actions, overruns, backup restoration, interventions.

## Beginning-to-end expectations

Configure market/money/policy -> verify market and buyer demand -> source property
-> resolve evidence gaps -> communicate permissibly -> confirm seller needs
-> underwrite -> approve offer -> execute documents/title -> secure buyer exit
-> coordinate closing -> confirm funds -> reconcile net -> evaluate outcomes.

No response, refusal, poor economics, title issues and failed funding are valid
stop states. Autopilot must stop appropriately as well as advance.

First live milestone: one real property with sourced facts, defensible economics,
verified buyer demand and an owner-approved decision. First business milestone:
one completed transaction with reconciled net profit. No guaranteed date/profit.

A complete initial autopilot operates all configured Operations in one selected
jurisdiction/strategy, recovers from failures, keeps outreach within enabled
authority, assists replies/training, versions button-refreshed knowledge,
routes commitments correctly, and reconciles actual money. A dashboard,
geocoder or email button alone does not meet this definition.

## Supporting documents

- KNOWLEDGE_AND_AUTOMATION_SPEC.md — data, tools, refresh and email contracts
- SALES_AND_COMMUNICATION_TRAINING.md — questions, templates and training tests
- IMPLEMENTATION_ROADMAP.md — status, build order and acceptance criteria
- SOURCE_REGISTER.md — primary sources, limitations and pending verification
