# Daily acquisition automation

The owner can enable daily shortlist updates at 9 AM America/Indiana/Indianapolis
and private representative drafts in `/sentras.html`. Save uses the displayed
buying criteria and handoff client/company/contact/notes. Activation queues an
initial cycle; later checks run daily. Stop prevents future cycles; an already
started provider request may finish. No messages, offers, commitments or
transaction execution are connected to this scheduler.

Settings, next due time, leases and cycle history persist in SQLite. A separate
server thread runs regardless of Meta discovery/health settings. A 20-minute
cross-process lease prevents concurrent workers; abandoned work reuses the
existing brief collector keys and packet content keys. Identical enabled settings
do not reset the schedule. Settings revisions prevent an old worker from creating
a packet after stop or after a settings change, including a check inside the
packet transaction.

Existing brief reuse applies for 24 hours. Fresh cycles require room for the
maximum four RentCast requests under the shared monthly cap (default 40); the
underlying collector also reserves quota atomically. No cap is increased. Missing
credentials, source kill switches and insufficient monthly budget produce visible
paused outcomes, checked again the following morning. Errors save only exception
types and retry the next morning. The scheduler creates no external notifications.

Only cards meeting the assumed yield target are included automatically. Missing
client/company details pause draft preparation, not research. Names do not imply
engagement, representation, funding or verified buyer demand. Repeated use of the
same saved evidence and packet settings returns the same private draft.

Changes compare addresses, asking prices, lower rent estimates and modeled yield.
Absence from a three-listing sample does not imply a sale or changed availability.
The automation panel displays the recent cycle results and changes; saved briefs
and handoff downloads remain in their existing panels. Reload to open new results.

Deployment: back up SQLite, verify migration on a copy, restart `clubsp.service`,
then enable using `/api/sentras/automation` with `enabled: true`, saved criteria,
`generate_handoffs: true` and `confirm_external_request: true`. Do not invent
client or representative names. GET this route is read-only; POST requires the
existing same-origin and owner access rules. Automation is disabled by default.
