# Revenue Command Center

The Revenue Command Center at `/command` is a read-only owner focus queue. It
does not create a second scoring system. It composes the current outputs already
produced by the Opportunity Queue, Commitment Graph, Funding Desk, Sourcing, and
Relationship Desk.

Priority is intentionally operational:

1. deals whose existing checks have reached owner review,
2. overdue and due relationship follow-ups, including a current approved draft
   only when Gmail send permission is actually enabled,
3. active deals with recorded blockers or required next actions,
4. research candidates that already match current standing buyer mandates.

The page preserves existing evidence scores, downside forecasts, criteria-fit
buyer counts, blockers, and next actions. Those values keep the semantics of
their source modules. A readiness score is not a probability of profit or
closing. Forecast dollars are not realized revenue.

The command center has no write endpoint and sets `execution_authorized=false`.
It cannot send email, make offers, spend money, reserve buyer capacity, sign
documents, change stages, or mark evidence reviewed. Each item links back to its
source workspace where the normal owner-review controls remain in force.
