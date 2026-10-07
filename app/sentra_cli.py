"""Operator entry point: python -m app.sentra_cli --db PATH list|run|evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .service import Application
from .sentra_runtime import load_sentra_environment


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run and inspect the first 25 Sentra collectors")
    parser.add_argument("--db", default="data/clubsp.sqlite3", help="Use the same database path as the running app")
    parser.add_argument("--env-file", default=".env", help="Optional local environment file")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List collectors, readiness and health without network calls")
    run = sub.add_parser("run", help="Explicitly run one bounded source collector")
    run.add_argument("sentra_id")
    run.add_argument("--input", default="{}", help="Collector input as JSON; credentials come from environment")
    run.add_argument("--force-refresh", action="store_true")
    run.add_argument("--idempotency-key")
    evidence = sub.add_parser("evidence", help="Read a saved execution and its provenance")
    evidence.add_argument("run_id")
    args = parser.parse_args(argv)
    load_sentra_environment(Path(args.env_file))
    app = Application(Path(args.db))
    try:
        if args.command == "list":
            result = app.sentra_state()
        elif args.command == "run":
            result = app.run_sentra({"sentra_id": args.sentra_id, "input_data": json.loads(args.input),
                "force_refresh": args.force_refresh, "idempotency_key": args.idempotency_key,
                "confirm_external_request": True})
        else:
            result = app.sentra_evidence(args.run_id)
    except (ValueError, LookupError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 1 if result.get("status") in {"failed", "interrupted"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
