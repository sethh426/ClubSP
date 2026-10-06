"""Synthetic source responses used to exercise the actual lifecycle and kernel."""
import json
from app.meta_source_transport import SourceResponse

from app.meta_sentras import SourceCandidate


SOURCE_URL = "https://example.gov/synthetic-parcels.json"
PAYLOAD = {"features": [{"attributes": {"parcel_id": "SYNTHETIC-001", "sale_price": 125000}}]}


def synthetic_response(payload=None):
    return SourceResponse(SOURCE_URL, "application/json", json.dumps(PAYLOAD if payload is None else payload).encode(), {}, 1)


def activate_synthetic(app, monkeypatch, *, sentra_id="example_sales", daily_limit=20, mode="official_api"):
    monkeypatch.setattr("app.meta_sentry_service.fetch_source", lambda *a, **kw: synthetic_response())
    candidate = SourceCandidate(discovery_provider="data_gov", external_id="synthetic-" + sentra_id,
        name="Synthetic Example County Parcel Sales", source_url=SOURCE_URL,
        description="parcel assessor property sales", jurisdiction_hint="Example County",
        capabilities_hint=("parcel_identity", "assessment", "sale_event"))
    app._store_discovered_candidates("data_gov", "synthetic parcels", [candidate], "a" * 64)
    app.meta_probe({"fingerprint": candidate.fingerprint})
    app.meta_propose({"fingerprint": candidate.fingerprint})
    app.meta_review({"fingerprint": candidate.fingerprint, "decision": "approve",
                     "note": "Synthetic test only: reviewed bounded schema, rights and zero-cost source"})
    app.meta_activate({"fingerprint": candidate.fingerprint, "sentra_id": sentra_id,
        "family": "parcel_assessor", "acquisition_mode": mode, "jurisdiction": "Example County",
        "source_type": "public_record", "rights_note": "Synthetic test rights reference",
        "estimated_cost_class": "free", "max_cost_per_run_cents": 0,
        "daily_request_limit": daily_limit, "coverage_markets": ["Example County", "Example City, IN"]})
    monkeypatch.setattr("app.sentra_execution.fetch_source", lambda *a, **kw: synthetic_response())
    return candidate
