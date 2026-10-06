# Scheduled sheriff-sale discovery refresh

ClubSP can refresh the Allen County Sheriff mortgage-foreclosure source unattended without creating a property, deal, offer, bid, reservation, or spend action.

Run:

```sh
python -m app.discovery_refresh --db data/clubsp.sqlite3
```

Only the `sheriff_sales` source is allowlisted for unattended refresh. ACCDC and North Campus remain reviewed/manual sources because the county CivicPlus edge blocks the server and those sources use the reviewed-snapshot workflow.

The command uses the same official-source parser, 24-hour cache, six-attempt daily budget, parcel-resolution logic, and research-priority logic as the UI. Output is intentionally bounded to status/count metadata; it does not print the candidate list or any credentials.

A successful scheduled/no-active-sale result exits 0. Source/parser failures exit nonzero.

## Example user cron

The live host can safely run the following under the unprivileged ClubSP user:

```cron
17 11 * * * cd /home/ubuntu/ClubSP && /usr/bin/python3 -m app.discovery_refresh --db data/clubsp.sqlite3 >> /home/ubuntu/clubsp-discovery-refresh.log 2>&1
```

11:17 UTC is early morning in Fort Wayne during daylight-saving time. The exact minute is intentionally non-round. The existing 24-hour cache prevents repeated work if an owner-triggered check already ran recently.

This job is research automation only. Candidate records still require source/status review, parcel/owner/title review, economics, buyer demand, and owner authorization before any transaction action.
