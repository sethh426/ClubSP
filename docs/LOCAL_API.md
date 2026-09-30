# Local application API

Run `python -m app.server` before calling endpoints. Responses are JSON except
for the three UI asset paths. Requests that mutate data require
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
