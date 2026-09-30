# Local application API

Run `python -m app.server` before calling endpoints. Responses are JSON except
for the fixed UI asset paths. Requests that mutate data require
`Content-Type: application/json`. Browser Origin must match the app URL.
Bodies are limited to 64 KiB.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | /api/health | Local server health |
| GET | /api/state | Properties and all memory collections |
| POST | /api/properties | Add an address |
| POST | /api/facts | Save a manual source and property fact atomically |
| POST | /api/predictions | Save a manual property estimate and fact references |
| POST | /api/predictions/{id}/outcome | Save outcome evidence and resolve an estimate atomically |

## Payload examples

Property:

```json
{"address":"123 Example St","city":"Fort Wayne","state":"IN","zip":"46802"}
```

Fact (replace property_id with the saved property's id):

```json
{"property_id":"PROPERTY_UUID","attribute":"sqft","value":1800,"provider":"County assessor","confidence":0.9}
```

Optional fact fields: `url`, `supersedes_fact_id`. Supersession requires the same
subject and attribute and preserves both records. The API does not infer that
newly recorded facts supersede older ones. Confidence defaults to 0.5.

Estimate:

```json
{"property_id":"PROPERTY_UUID","prediction_type":"repair_cost","predicted_value":30000,"confidence":0.7}
```

Outcome:

```json
{"actual_value":33000,"provider":"Contractor invoice","confidence":1}
```

Estimate and outcome values must be finite and nonnegative. Confidence must be
between 0 and 1. UUIDs must be valid. Unknown properties or predictions return
404; validation errors return 400; foreign hosts/origins return 403.

The outcome quality score combines observed numeric error with the confidence
entered for its evidence. It does not verify the evidence or change future
estimates. The snapshot references record which facts were present when an
estimate was saved.

## Deal Operations and money

All new writes retain the same JSON, Host/Origin and local-only controls.

| Route | Fields and result |
| --- | --- |
| POST /api/deals | property_id, strategy; creates a deal and eight operation tasks |
| POST /api/deals/{id}/underwriting | Property type, basis and every cost/value input; saves scenario version |
| POST /api/deals/{id}/financial-plan | seller_price, assignment_fee, planned_cash_at_risk, max_cash_at_risk, basis; records fixed-price forecasts and stage blockers |
| POST /api/deals/{id}/ledger | UUID entry_key, kind, category, positive dollar amount (max 2 decimals), occurred_on, note, evidence_reference; retry-safe cash record |
| POST /api/deals/{id}/reconciliation | owner_confirmed_complete=true, note, evidence_reference; requires ended deal, resolved escrow and received funds for completed deals |
| POST /api/deals/{id}/tasks | operation (1–18), title, owner, expected_result, kind, blocking_stage, optional due_on |
| POST /api/tasks/{id}/status | status=open/done, note, completion evidence_reference |
| POST /api/tasks/{id}/schedule | owner, due_on (or empty), note |
| POST /api/deals/{id}/stage | Stage plus note; contracted/completed need owner confirmation and evidence; finance/task gates enforced |
| POST /api/buyers | Name, markets, strategies, types, price/repair limits and owner funding status |
| POST /api/deals/{id}/buyer-matches | Empty object; assignment compares proposed seller price + fee when recorded, resale compares expected exit price |

Ledger kinds: expense, income, escrow_deposit, escrow_return, escrow_applied,
escrow_forfeit. A correction includes reversal_of and repeats the original kind,
category and amount with a new entry_key and correction evidence. A reversal
cannot leave escrow negative at any recorded date. Same-key retries return the
same record; conflicting reuse is rejected. Evidence references are private
record identifiers, not downloaded or independently verified documents.

GET /api/state now includes each deal's finance/tasks/history, the 18 Operations,
Indianapolis business date and a scorecard. A changed ledger invalidates the old
reconciliation; historical records stay intact.

## Official property research

| Route | Fields and result |
| --- | --- |
| POST /api/properties/{id}/research | parcel_key; one exact Allen County lookup, or cached snapshot; returned facts remain pending |
| POST /api/research/{id}/review | decision=accept/reject, note; acceptance requires owner_confirmed_identity=true, confidence (default 0.8), and mismatch_explanation when identity fields differ or are missing |

GET /api/state includes `research` snapshots and the fixed `providers` registry.
Only keys containing eighteen digits starting with `02` are supported. The
property must be in Indiana. Pending/accepted snapshots are cached for 24 hours;
the daily workspace limit is twenty requests including failures. Acceptance is
idempotent and imports only available fields with provenance. A pending snapshot
older than seven days must be refreshed before acceptance. Rejection imports no
facts. Records do not authorize outreach or establish title or valuation.

## Conversations, drafts and practice

| Route | Fields and result |
| --- | --- |
| POST /api/contacts | property_id, name, optional email, role=unverified/owner/representative/other; owner/representative need role_reference; starts unknown or existing-email suppression |
| POST /api/contacts/{id}/permission | status=unknown/permitted/suppressed, note, evidence_reference required unless unknown; suppression cannot be cleared |
| POST /api/contacts/{id}/messages | UUID message_key, direction=incoming/outgoing/note, channel=email/phone/in_person/note, category, body, occurred_on, evidence_reference; retry-safe history; incoming stop signals cancel drafts and suppress matching emails |
| POST /api/contacts/{id}/profile | status=confirmed/hypothesis, goal, optional timing/condition_notes/authority_notes/alternatives/priority, pain_points list; confirmed requires evidence_reference; saves a new profile version |
| POST /api/contacts/{id}/reply | Empty object; local template draft from saved incoming context; refuses suppressed contacts or an already-followed-up incoming message |
| POST /api/replies/{id}/review | owner_reviewed=true, final_body, note; requires current context and permitted contact; immutable review, no external send |
| POST /api/training/practice | UUID attempt_key, scenario_id, response, ratings object, hard_failures list, review_note; retry-safe owner assessment |

GET /api/state includes `communications` and `training`. Conversation dates
cannot be in the future. Category/lesson/rubric choices are provided in state.
Pain-point choices: price, timing, convenience, trust, condition, authority, other.
Practice ratings must include listening, grounding, clarity, next_step, economics,
respect, each an integer 0–2. A hard failure overrides the practice threshold;
the result never approves an external template. Email transport is not configured.

Recorded message/profile/permission/deal/underwriting/financial-plan changes make
older draft context stale. Suppression preserves prior reviewed text and notes
while changing draft status to void. Unique-email suppression applies throughout
the workspace; no outreach or email-provider call occurs in these routes.

## Knowledge source checks and versions

| Route | Fields and result |
| --- | --- |
| POST /api/knowledge/update | UUID request_key, source_ids (1–8 fixed ids from state), jurisdiction, initiated_by; starts one explicit source-check job; same-key retries return the same run |
| POST /api/knowledge/runs/{id}/cancel | Empty object; stops later requests and prevents an in-flight result from being staged after cancellation |
| POST /api/knowledge/snapshots/{id}/review | decision=accept/reject, reviewer, note; accept additionally needs owner_verified_source=true, title, claim, claim_type, applicability, review_reference, optional published_on/effective_on, professional_review_reference required for compliance |
| POST /api/knowledge/items/{id}/version | action=activate/withdraw, owner_reviewed=true, reviewer, note, evidence_reference; preserves versions and an audit event |

GET /api/state includes `knowledge` with sources, coverage gaps, request usage,
runs/snapshots, reviewed items and events. Run states are requested, running,
review_required, partially_complete, failed and cancelled. Source checks are
queued/fetching/succeeded/failed/cancelled. A successful check is not a reviewed
business claim. Published claim_type is interpretation, operational_note or
source_metadata; items never change execution policy.

Only one run can be active. Fixed URLs/ids, a six-second timeout, 1 MiB response
limit, no redirects/retries, 24-hour cache and 20 attempted source requests per
business day bound research. Cached retrieval dates are preserved. Empty/invalid
metadata and challenge pages fail. Rejected or failed checks leave active items
unchanged. Checks over seven days old or superseded by changed source content
cannot publish as current. Restart fails unfinished work without external retry.

An active note change/rollback/withdrawal invalidates old reply context. Related
sales/operations/compliance notes can appear beside training. References and
professional review are owner-entered records, not independent verification.
