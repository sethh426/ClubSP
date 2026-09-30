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
