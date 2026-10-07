"""Read-only provider smoke checks in an isolated, disposable workspace.

This command never approves/activates sources, runs paid Actors, imports property
facts, or changes a production workspace. Use it on a host with outbound HTTPS.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from .service import Application


def live_check():
    results = {"providers": {}, "schema_probe": None, "production_activation": False}
    with tempfile.TemporaryDirectory(prefix="clubsp-meta-check-") as temporary:
        application = Application(Path(temporary) / "check.sqlite3")
        for provider, query in (("arcgis_online", "Indiana parcels"), ("apify_store", "property")):
            try:
                result = application.meta_discover({"provider": provider, "query": query})
                results["providers"][provider] = {"status": "success", **result}
            except ValueError as exc:
                results["providers"][provider] = {"status": "failed", "error": str(exc)}
        state = application.meta_sentra_state()
        attempted = 0
        for candidate in state["candidates"]:
            if candidate["discovery_provider"] != "arcgis_hub" or attempted >= 3:
                continue
            if "/rest/services/" not in candidate["source_url"].lower():
                continue
            attempted += 1
            try:
                result = application.meta_probe({"fingerprint": candidate["fingerprint"]})
                if result.get("layer_candidates"):
                    result = application.meta_probe({"fingerprint": result["layer_candidates"][0]["fingerprint"]})
                if result["state"] == "schema_probed":
                    results["schema_probe"] = {"status": "success", "field_count": len(result["schema"]["fields"]),
                                               "schema_fingerprint": result["schema_fingerprint"]}
                    break
            except ValueError:
                continue
        results["data_gov_readiness"] = state["provider_readiness"]["data_gov"]
        results["ready"] = (all(p["status"] == "success" for p in results["providers"].values())
                            and results["schema_probe"] is not None)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True, help="Run bounded public catalog and schema checks")
    parser.parse_args()
    result = live_check()
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["ready"] else 1)


if __name__ == "__main__":
    main()
