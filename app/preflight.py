"""Deployment preflight and readiness checks for ClubSP."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3

from .auth import OwnerAuth
from .gmail import GmailConnection, load_local_environment


def database_ready(path):
    path = Path(path)
    if not path.exists():
        return False, "database_missing"
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as connection:
            row = connection.execute("PRAGMA quick_check").fetchone()
            if not row or row[0] != "ok":
                return False, "database_integrity"
            required = {"properties", "deals", "memory"}
            tables = {r[0] for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            if not required.issubset(tables):
                return False, "database_schema"
    except sqlite3.Error:
        return False, "database_unavailable"
    return True, "ok"


def deployment_readiness(database_path, gmail=None, auth=None, environment=None):
    environment = (environment if environment is not None else os.environ.get("CLUBSP_ENV", "development")).strip().lower()
    if environment not in {"development", "production"}:
        return {"ready": False, "environment": environment, "checks": {"environment": "invalid"}}
    auth = auth or OwnerAuth()
    gmail = gmail or GmailConnection(Path(database_path).parent / "private")
    db_ok, db_status = database_ready(database_path)
    checks = {"database": db_status, "owner_auth": "enabled" if auth.enabled else "disabled"}
    ready = db_ok
    if environment == "production":
        if not auth.enabled:
            ready = False
            checks["owner_auth"] = "required"
        if gmail.external_callback:
            checks["https_callback"] = "configured"
        else:
            ready = False
            checks["https_callback"] = "required"
        if gmail.external_callback and not gmail.external_callback.startswith("https://"):
            ready = False
            checks["https_callback"] = "invalid"
    else:
        checks["https_callback"] = "configured" if gmail.external_callback else "not_required"
    return {"ready": ready, "environment": environment, "checks": checks}


def main():
    parser = argparse.ArgumentParser(description="Validate ClubSP deployment readiness")
    parser.add_argument("--db", default="data/clubsp.sqlite3")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    load_local_environment(args.env_file)
    result = deployment_readiness(args.db)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["ready"] else 1)


if __name__ == "__main__":
    main()
