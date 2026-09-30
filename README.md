# ClubSP

Wholesale Autopilot — memory, evidence, reasoning, workflows, and integrations.

## Current implementation

The Python core stores sources, facts, predictions, observations, learning records,
and workflow events in memory. It validates source references, confidence values,
duplicate record IDs, and fact supersession references. Outcome resolution checks
the subject and timing, retains the observation, and rejects repeated resolution.

Learning confidence is a heuristic combining numeric prediction error and outcome
confidence; it is not a calibrated probability. Fact supersession preserves history;
callers must decide which fact to use. These checks apply to store/engine methods;
direct edits to public dictionaries or mutable records bypass them.

## Run tests

Requires Python 3.11 or newer:

```sh
python -m pip install "pytest>=8,<9"
python -m pytest -q
```

GitHub Actions runs the suite on Python 3.11, 3.12, and 3.13.

## Next milestones

1. Persistent storage with transactional outcome resolution.
2. Property and source ingestion with normalized, traceable evidence.
3. Provider routing, usage limits, and API/MCP adapters.
4. Resumable workflow execution and an application interface.

External API integrations and a running user interface are not implemented yet.
See [architecture](docs/ARCHITECTURE.md) and [integration strategy](docs/INTEGRATIONS.md).
