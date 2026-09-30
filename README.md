# ClubSP

A working local property workspace for evidence, estimates, outcomes, and learning.

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
2. Record facts such as square footage or asking price with a source name,
   optional source URL, and confidence from 0 to 1.
3. Save a repair-cost, sale-price, or days-to-close estimate.
4. Enter the actual value and its evidence source when the result is known.
5. Review the estimate error and the attached learning history.

Facts and outcomes are manually entered. Source URLs are saved references;
the app does not fetch or verify their content. Estimates are supplied by you,
not generated valuations. Learning records track numeric accuracy and evidence
confidence; this is not model training or a calibrated probability.

SQLite stores properties and all six memory collections. Outcome resolution is
transactional: source, observation, prediction update, and learning record either
all commit or all roll back. Concurrent duplicate outcomes cannot create duplicate
learning records. Restarting the app preserves history and subject indexes.

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

## Next integrations

Provider adapters, live property-data ingestion, automated underwriting, MCP
transport, outreach, and deal/contract workflows remain future work.
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

Researched knowledge refresh is specified as an explicit **Update Knowledge**
button, without continuous background research. Email/reply/closing events remain
operational events. Live external actions require configured providers and the
applicable operating authority and policies.
