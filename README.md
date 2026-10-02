# ClubSP

Gmail local authorization is available through the **Gmail · Connect your mailbox**
panel. See [Gmail setup](docs/GMAIL.md) for the exact callback, Google test-user
configuration, private token storage, and limitations. Email sending, conversation
import, token refresh, and follow-ups are not enabled by this release.

A working local property workspace for deal operations, official parcel evidence,
underwriting, owner cash and profit, seller conversations and sales practice.

## Run the app

Install Python 3.11 or newer, download or clone this repository, and run from its root:

```sh
python -m app.server
```

Open **http://127.0.0.1:8000** in your browser. No runtime packages or paid API keys
are required. Stop the server with Ctrl+C. Your data remains in
`data/clubsp.sqlite3` and is loaded again when you restart.

Use another port or database file if needed:

```sh
python -m app.server --port 8080 --db data/my-workspace.sqlite3
```

This is a single-user local app. The server binds to the loopback interface,
validates Host/Origin headers, and serves only fixed assets. It has no login or
production hosting configuration. Use an authenticated production server before
making it accessible remotely.

## Use the workspace

1. Add a property with its address, city, state, and optional ZIP code.
2. Record facts with a source name, optional URL, and confidence. For an Allen
   County, Indiana property, use **Official property record** to look up its exact
   18-digit parcel key, compare the returned identity and accept or reject it.
3. Start an assignment or resale deal, enter your assumptions, and review downside,
   base, and upside scenarios. These are manual estimates, not valuations or offers.
4. Add buyers with their markets, strategy, price/repair limits, and owner-reviewed
   funding reference; run matching to compare stated criteria.
5. Move deal stages with notes. Contracted and completed stages require your
   confirmation plus an evidence reference.
6. Save proposed seller terms and your peak owner cash exposure/limit. Contracted
   stages check the current plan, underwriting ceiling, net target and cash limit.
7. Use **Operations & exceptions** to assign owners/deadlines, record completion
   evidence and resolve the required diligence, agreement and closing tasks.
8. Record actual costs, receipts and earnest-money movements. Correct mistakes by
   reversing the original entry, preserving the audit record.
9. After completion or loss, resolve escrow and confirm all costs/receipts are
   recorded; reconcile actual contribution against the fixed-price forecast.
10. Track ordinary estimates and their actual outcomes in the learning workspace.
11. Add seller/decision-participant contacts, record permission evidence and actual
    conversations, and save confirmed priorities separately from hypotheses.
12. Request a local reply draft, review/edit it, and practice synthetic objections
    in the sales training section.
13. Use **Update Knowledge** to check selected public sources, review a bounded
    preview against the original, publish your internal note, and restore or
    withdraw versions when needed.

Facts and outcomes can be manually entered. The official parcel adapter fetches
only its fixed county endpoint; other source URLs are saved references and their
content is not fetched. Estimates are supplied by you,
not generated valuations. Learning records track numeric accuracy and evidence
confidence; this is not model training or a calibrated probability.

SQLite stores properties, deals, operation tasks, money/reconciliation records and all six memory collections. Outcome resolution is
transactional: source, observation, prediction update, and learning record either
all commit or all roll back. Concurrent duplicate outcomes cannot create duplicate
learning records. Restarting the app preserves history and subject indexes.

## Cash and Operations

The ledger stores money in integer cents and distinguishes paid expenses, actual
income, escrow deposits, refunds, applications and forfeitures. Deposits are cash
at risk while held; they become costs only when applied or forfeited. An applied
purchase deposit is recorded separately from the remaining cash purchase payment.

Recorded contribution is **pre-tax** and excludes overhead unless you enter its
allocation. The portfolio scorecard includes only current owner-confirmed
reconciliations in reconciled contribution. Costs from lost deals count too.
Corrections invalidate old reconciliations until reviewed again. Reconciliations
are record comparisons, not independent bank verification or model training.

The 18 Operations catalog shows the expected process and current capability.
Every deal has eight initial tasks; the recorded underwriting, money plan and
reconciliation complete their own tasks. Other reviews require owner-entered
completion evidence. Deadline/exception visibility is local; no email reminders
or external jobs run yet.

## Official parcel research

The first adapter covers Allen County, Indiana through its official iMap service.
Enter a parcel key starting with `02`, optionally separated by hyphens. One exact
record is requested with a ten-second timeout, a response-size limit, no automatic
retries, twenty requests per business day and a twenty-four-hour cache. No API key
or paid provider is required. Failed attempts also use the daily budget.

Reported owner-of-record, site address, transfer date and record year are staged
for review. Compare the parcel and address before accepting; explain identity
discrepancies or reject the record. Acceptance saves source URL, retrieval time,
content hash and facts while preserving superseded evidence. Missing fields stay
unknown. These records do not establish current title, seller authority, contact
permission, property value or comparable sales.

County metadata and a live no-match query were checked. Positive-record behavior
is tested with explicitly synthetic records; a real parcel must still be compared
with the original record. Browser CI uses an injected test adapter and never
queries real owners or consumes the live request budget.

## Seller conversations and training

Contacts begin with unknown permission. Owner/representative roles and permitted
contact require evidence references. Incoming messages are entered manually with
their channel, date and source reference. Explicit stop/wrong-person categories
and a bounded set of stop phrases suppress the contact, cancel all its drafts and
suppress matching email addresses across properties. There is no suppression
reset in this release. The phrase check is not a complete language classifier;
record ambiguous refusals as stop requests rather than assuming eligibility.

Save seller goals, timing, condition, decision participants, alternatives and
priorities with a confirmed/hypothesis basis. The pain-point view counts each
contact's latest profile once and excludes hypotheses from confirmed counts.
It describes your records, not local market prevalence or causes of a sale.

Reply suggestions use internal local templates and the recorded concern category,
with conversation/profile/deal/financial references. They do not call an AI model,
invent terms or send email. Changed context invalidates a previous draft; owner
review requires current context and permission evidence. Reviewed text and notes
remain in history even if a later stop request voids the draft. A reviewed draft
is not a campaign clearance or completed message.

Twelve synthetic lessons cover discovery, price, timing, trust, role, authority,
condition, funding, paperwork, stop requests, legal questions and payment changes.
Record a practice response and your six-dimension self-assessment. A recorded hard
failure overrides the 10/12 practice threshold. Scores are owner assessments,
not automated grading, model training or proof of sales effectiveness. The
curriculum remains an internal draft pending review for external use.

## Button-triggered knowledge checks

The Update Knowledge panel registers eight fixed public sources across sales,
market context, compliance, financing education, closing, business planning and
property-service metadata. A button starts one durable, bounded source-check run;
opening the app does not start research. Requests use a six-second timeout, 1 MiB
response limit, no redirects/retries, a twenty-attempt daily knowledge budget and
a twenty-four-hour cache. These limits are separate from the parcel lookup budget.

The app compares content/section fingerprints, saves a short preview, reported
publication date when present, actual retrieval time and per-source success or
failure. A cached check keeps its original retrieval date. Cancel stops later
requests; an in-flight request may finish before the worker stops. Restart marks
interrupted runs failed and never resumes external research silently.

This release is **source checking plus owner-authored review**, not automatic
research synthesis. Open the original source, write a bounded paraphrased note
and its applicability/limits, and save review evidence. Compliance notes require
a professional-review reference; that is an entered record, not independent
certification. Retrieval, publication and effective dates are separate. Failed
or changed sources do not replace active notes or certify jurisdiction coverage.

Published notes retain versions, reviewer evidence, withdrawal/restore events and
source-change/staleness flags. Training can show related internal notes, and a
published/changed active version invalidates old reply context. No refresh sends
email, changes a saved deal assumption or grants contract/payment authority.

Public source pages were located through research tools. Direct runtime fetches
of several pages and the later metadata check were blocked in this execution
environment; those failures are retained. Source extraction and the complete
workflow are validated offline with explicitly synthetic pages. Run the app with
working source access and compare returned pages before publishing real notes.
Licensed comps/local time series, current assignment/disclosure applicability,
tax/accounting and transaction-specific reviews remain coverage gaps.

## Test

```sh
python -m pip install "pytest>=8,<9"
python -m pytest -q
```

Optional browser tests require Node.js 22:

```sh
npm install --no-save @playwright/test@1.55.1
npx playwright install chromium
npx playwright test
```

GitHub Actions runs the Python suite on 3.11–3.13 and browser workflows at desktop
and mobile sizes, including reload persistence, rendered evidence, and outcomes.

## Back up your workspace

Stop the app and copy `data/clubsp.sqlite3` to a safe location. To restore it,
stop the app and replace the database file with the backup. Databases are ignored
by Git so property data stays out of source control.

## Structure

- `core/memory/`: evidence and learning domain logic
- `app/database.py`: SQLite persistence and atomic sessions
- `app/service.py`: property and memory application operations
- `app/server.py`: local HTTP API and static UI server
- `app/static/`: responsive browser workspace
- `tests/`: domain, persistence, HTTP, concurrency, and browser tests

## Opportunity queue

The evidence-backed queue assesses entered pre-contract deals against your saved
buy box and cash/downside review limits. It explains missing evidence, conflicting
facts, changed underwriting context, current buyer-criteria fit and next reviews.
It does not source leads or authorize offers, messages or spending. See
[opportunity queue scope and limitations](docs/OPPORTUNITY_QUEUE.md).

## Candidate intake and sale evidence

Stage authorized CSV exports, review individual candidate identities, and attach
reviewed completed sales to subject properties. Underwriting captures the current
sales; changes invalidate saved comparisons and require review before contracting.
See [source formats, review boundaries and local API](docs/SOURCING.md).

## Remaining integrations

General lead sourcing, licensed comps, independent funding verification, MCP transport,
email delivery/inbound sync, and signed document/transaction execution remain future work.
The full-snapshot persistence adapter suits a small local workspace; larger
datasets need targeted queries, migrations, and a production database strategy.

See [architecture](docs/ARCHITECTURE.md), [integration strategy](docs/INTEGRATIONS.md),
and [local API](docs/LOCAL_API.md).

## Personal acquisition and resale operations

ClubSP is Seth's personal system for finding, evaluating, acquiring/assigning,
and reselling property opportunities. The business goal is completed transactions
and reconciled net profit, not selling software subscriptions.

Start with the [Master Operations Playbook](docs/MASTER_OPERATIONS_PLAYBOOK.md).
It defines 18 Operations and the target autopilot; it does not claim future
integrations are implemented.

- [Implementation roadmap](docs/IMPLEMENTATION_ROADMAP.md)
- [Knowledge and automation specification](docs/KNOWLEDGE_AND_AUTOMATION_SPEC.md)
- [Sales and communication training](docs/SALES_AND_COMMUNICATION_TRAINING.md)
- [Research source register](docs/SOURCE_REGISTER.md)

**Update Knowledge** now starts bounded source checks and an owner-review
workflow, without continuous background research. Automatic synthesis and legal
execution-policy updates remain future work. Live external actions require
configured providers and the applicable operating authority and policies.
