# Release evidence

## Operations and money — 2026-09-30

Delivered: proposed seller terms, owner cash limits, fixed-price assignment/resale
forecasts, integer-cent cash/escrow ledger, retry-safe writes, auditable reversals,
completion/loss reconciliation and current-only portfolio contribution totals.
The 18 Operations catalog, seeded/custom tasks, exceptions, deadlines and review
records are now visible in the browser. Contract stage gates require current
financial assumptions and completed owner reviews.

Validation: 45 Python tests and two desktop/mobile browser workflows passed locally.
GitHub Actions is also required before merge. Synthetic test records are isolated
in the ignored browser-test database; there are no seeded live opportunities.

Storage upgrade: existing property/evidence databases keep their records; new
finance/task tables and indexes are added automatically. Back up a stopped
workspace before upgrading. Downgrading preserves the new tables but older code
will not show or enforce the money/task gates.

Limits: owner-entered figures and evidence references are not bank, title or funds
verification. Contribution is pre-tax; overhead is included only when entered.
No live research, email, e-sign, funds transfer or paid provider is activated by
this release. Task deadlines are shown locally; external reminders are pending.
