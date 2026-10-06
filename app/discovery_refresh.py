"""Safe CLI for scheduled refresh of official ClubSP discovery sources."""
from __future__ import annotations

import argparse
import json

from .service import Application


SAFE_AUTOMATED_SOURCES = {"sheriff_sales"}
GOOD_STATUSES = {"scheduled_sales", "no_active_sales"}


def refresh_source(application, source_id="sheriff_sales"):
    if source_id not in SAFE_AUTOMATED_SOURCES:
        raise ValueError("This source is not approved for unattended refresh")
    result = application.check_discovery({"source_id": source_id})
    status = result.get("status")
    summary = {
        "source_id": source_id,
        "status": status,
        "cached": bool(result.get("cached")),
        "candidate_count": len(result.get("candidates") or []),
        "parcel_resolution_count": int(result.get("parcel_resolution_count") or 0),
        "changed": bool(result.get("changed")),
        "fetched_at": result.get("fetched_at"),
        "execution_authorized": False,
        "creates_deals": False,
    }
    if status not in GOOD_STATUSES:
        raise RuntimeError(json.dumps(summary, sort_keys=True))
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Refresh one explicitly approved official ClubSP discovery source"
    )
    parser.add_argument("--db", default="data/clubsp.sqlite3")
    parser.add_argument("--source", default="sheriff_sales", choices=sorted(SAFE_AUTOMATED_SOURCES))
    args = parser.parse_args()
    try:
        result = refresh_source(Application(args.db), args.source)
    except Exception as error:
        print(json.dumps({
            "source_id": args.source,
            "status": "error",
            "error_type": type(error).__name__,
            "execution_authorized": False,
            "creates_deals": False,
        }, sort_keys=True))
        raise SystemExit(1)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
