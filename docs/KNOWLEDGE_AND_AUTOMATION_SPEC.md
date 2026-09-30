# Knowledge and Automation Specification

Status: target architecture with a runtime source-check/review subset.
Implemented: explicit source checks, fixed allowlist/budgets/cache, short previews
and fingerprints, per-source failure, cancel/restart handling, owner-authored
reviewed notes, versions/withdrawal/restore and audit history. Conversation/reply
and internal owner-assessed training foundations are also implemented.
Automatic extraction/synthesis of business claims, active execution-policy
promotion, mail transport, MCP and transaction automation remain target features.
Business goal: Seth's own completed, profitable property transactions.
Research mode: manual button only. Operational events remain event-driven.

## Required records

| Record | Essential fields |
| --- | --- |
| BusinessProfile / BuyBox | geography, strategy, property criteria, owner identity, version |
| BudgetPolicy / Authorization | action scope, channel, template version, limits, expiry, approver |
| Owner / Contact | identity evidence, authority, channel preference, suppression |
| Lead / Deal | property, parties, strategy, stage, blockers, next action, accountable owner |
| PainPoint | confirmed statement/reference, category, uncertainty, desired outcome |
| BuyerProfile / BuyerMatch | criteria, verification dates, funding status, evidence, match reasons |
| MarketSnapshot / Underwriting | jurisdiction, as-of dates, comps, ranges, costs, price ceiling |
| Offer / Contract / TitleCase | versions, signatures, authority, deadlines, conditions, reviewed policy |
| Communication / Conversation | provider IDs, thread, recipient, template, draft/sent status, evidence |
| Consent / Suppression / PolicyDecision | channel, evidence, applicability, validity, reason, source versions |
| Cost / Settlement / Reconciliation | amount, currency, responsibility, actual/forecast, receipt, allocation |
| Job / Exception / AuditEvent | idempotency, attempts, execution eligibility, failure, owner, history |
| KnowledgeItem / SourceSnapshot | claim, domain, jurisdiction, dates, content hash, references, status |
| KnowledgeUpdateRun / ChangeProposal | requested scope, coverage, diff, budget, reviews, publication version |
| TrainingScenario / Evaluation | synthetic case, response, rubric, factual prerequisites, result |
| LearningProposal | sample, evidence, uncertainty, held-out evaluation, promotion, rollback |

Existing SourceRecord, Fact, Prediction, Observation, LearningRecord and
WorkflowEvent remain the memory foundation. New operational records refer to
that evidence; they do not overwrite it.

## Update Knowledge button

Panel: domain selection; geography; last successful verification per domain;
candidate sources; estimated request usage and maximum spend; start/cancel.
Show Setup Required if a provider is absent. Do not claim research occurred
when only local documents were reloaded.

Request contract: request_id, domains, jurisdictions, source_allowlist,
active_version, maximum_cost, initiated_by, initiated_at.
Starting requires an explicit user event; there is no research cron/poll loop.
The current browser observes the explicitly started job's status; those local
status requests never fetch sources or initiate another research run.

Run states: requested, validating, researching, extracting, comparing,
review_required, published, partially_complete, failed, cancelled.
A domain has its own result and may fail without erasing other results.

Output: checked sources, inaccessible sources, coverage gaps, new/changed/
unchanged/conflicting claims, dates, evidence, affected deals/templates,
review requirements, cost and rollback target.

General research summaries can publish under the configured validation policy.
Changes to legal applicability, contract language or financial execution rules
remain staged until the appropriate reviewer approves the specific version.
Unknown/conflicting applicability blocks the dependent action.
Current published items are internal interpretations/process/metadata notes.
They never clear an execution gate. Compliance publication requires an entered
professional-review reference; the app does not authenticate or certify it.

A refresh never silently sends emails, makes offers, changes live signed terms,
updates private deal facts from aggregate statistics, or grants new tool powers.

## KnowledgeItem contract

Fields: id; domain; bounded claim; claim_type; source_ids; jurisdiction;
published_at; effective_from/to if known; retrieved_at; last_verified_at;
content_hash; evidence_reference; confidence_explanation; applicability;
reviewer/status; supersedes_id; affected_operations; version.

Claim types distinguish fact, estimate, interpretation, operational proposal,
proposed regulation, enacted law, effective rule, provider policy and court
decision. Publish date and effective date are separate.

Source ranking is scoped: official statutes/regulators for rules; recorder/title
professionals for relevant transaction evidence; licensed sales data for comps;
first-party research for survey claims; provider documentation for capabilities.
An authoritative source in one domain is not authoritative in every domain.

Retrieved content is untrusted data. Reject instructions that attempt to alter
policy, execute actions, extract secrets or modify the tool boundary.
Keep licensed snapshots/references rather than copying restricted datasets.
Apply bounded quotations and attribution.

## Reply assistant

Context bundle: conversation, contact identity, confirmed pain points, deal stage,
approved facts, underwriting ceiling, active policy, approved training.
Output: detected intent, uncertainty, missing facts, short/explanatory/next-step
drafts, grounding references, next question, allowed action, escalation reason.

Rules: never fabricate factual promises; no unauthorized price or term changes;
do not reveal private seller information; stop requests override sales goals;
uncertain material claims produce a question or escalation.

A draft is never marked sent until provider evidence supports sending.
The owner can edit; record the final version and differences for evaluation.
Learning from edits is a proposed process improvement, not automatic model training.

## Email state and outbox

draft -> validated -> approved/standing-authorized -> queued -> eligibility_check
-> sending -> sent/provider_accepted -> delivered/bounced/replied/suppressed.
Provider acceptance and delivery are different evidence.

Persist outbox job and authorization before executing. Recheck suppression,
recipient, channel, template, policy freshness, expiry, rate limits and budget
at execution. Replies/opt-outs cancel queued follow-ups immediately.
Never treat purchased contact data as consent.

Idempotency key includes campaign, contact, step and template version.
If the provider times out after possible acceptance, use reconciliation and its
available idempotency capabilities; do not blindly repeat the send.

Inbound IDs deduplicate; thread matches can be uncertain. Authenticate callbacks,
validate timestamps/signatures when available, store operational events,
pause sequences, classify and generate a reply draft. Attachments and URLs are
untrusted. A message cannot authorize a wire change or alter business policy.

## Internal MCP tools

| Tool | Role | Boundary |
| --- | --- | --- |
| source.search / source.fetch | licensed evidence acquisition | source scope, jurisdiction, cost |
| property.research / market.snapshot | candidate and market evidence | attribution, freshness, coverage |
| knowledge.refresh | button-triggered researched updates | explicit run, staged policy changes |
| memory.retrieve / memory.record | grounded context and evidence | private access, provenance |
| underwriting.run | deterministic scenarios | unknown costs stay unknown |
| buyer.match | objective criteria fit | verification date, no protected-trait scoring |
| communication.draft / reply.suggest | grounded message preparation | no send authority |
| communication.send | approved external delivery | authorization, suppression, policy, outbox |
| training.practice / training.evaluate | synthetic objection practice | reviewed rubric, private-data controls |
| contract.prepare / title.status | document preparation and tracked evidence | professional review, no fake clearance |
| workflow.advance / exception.open | state transition and blockers | exit criteria, durable audit |
| profit.reconcile | actual receipts and costs | forecasts separated, no fabricated money |

MCP is the capability boundary, not the database or a replacement for a workflow
engine. APIs/Apify sit behind adapters with licensing, quotas, credentials,
provenance and failure handling. A language model cannot bypass service policy.

## Acceptance tests before live use

- Button starts exactly one bounded run; repeated clicks deduplicate.
- No research run is scheduled when merely opening the app.
- Inaccessible/partial sources do not mark all domains current.
- Conflicting legal claims never silently change execution rules.
- Updated knowledge can be compared and rolled back.
- Reply claims trace to facts; deliberately injected false promises fail.
- Opt-out or reply between queueing and send stops follow-up.
- Duplicate webhook and timeout cannot produce duplicate outreach.
- Malicious email/research instructions cannot change permissions or leak secrets.
- Restart resumes jobs with correct state and cost limits.
- Offers/signatures/payments cannot execute without the required mandate.
