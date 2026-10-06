import json

from app.meta_sentras import SourceCandidate
from app.service import Application


def test_meta_sentra_candidate_persists_and_requires_approval(tmp_path):
    app = Application(tmp_path / "clubsp.sqlite3")
    candidate = SourceCandidate(
        discovery_provider="data_gov",
        external_id="example-dataset",
        name="Example County Parcel Sales",
        source_url="https://example.gov/data.json",
        description="parcel assessor property sales",
        jurisdiction_hint="Example County",
        capabilities_hint=("parcel_identity", "assessment", "sale_event"),
    )
    saved = app._store_discovered_candidates(
        "data_gov", "parcel sales", [candidate], "a" * 64
    )
    assert saved["new_quarantined"] == 1
    state = app.meta_sentra_state()
    assert state["summary"]["quarantined"] == 1
    record = state["candidates"][0]
    assert record["state"] == "quarantined"
    assert state["automatic_activation"] is False


def test_approved_candidate_can_activate_into_dynamic_registry(tmp_path):
    app = Application(tmp_path / "clubsp.sqlite3")
    candidate = SourceCandidate(
        discovery_provider="data_gov",
        external_id="example-dataset",
        name="Example County Parcel Sales",
        source_url="https://example.gov/data.json",
        description="parcel assessor property sales",
        jurisdiction_hint="Example County",
        capabilities_hint=("parcel_identity", "assessment", "sale_event"),
    )
    app._store_discovered_candidates("data_gov", "parcel sales", [candidate], "b" * 64)
    fingerprint = candidate.fingerprint
    with app.database.session(write=True) as (connection, _):
        connection.execute(
            """UPDATE meta_source_candidates
               SET state='proposed',schema_fingerprint='schema123',
                   assessment_json=? WHERE fingerprint=?""",
            (json.dumps({"score": 90, "status": "promote_for_review"}), fingerprint),
        )

    reviewed = app.meta_review({
        "fingerprint": fingerprint,
        "decision": "approve",
        "note": "Reviewed source rights, schema and usefulness",
    })
    assert reviewed["state"] == "approved"

    activated = app.meta_activate({
        "fingerprint": fingerprint,
        "sentra_id": "example_county_sales",
        "family": "parcel_assessor",
        "acquisition_mode": "official_api",
        "jurisdiction": "Example County",
        "rights_note": "Reviewed test source rights",
    })
    assert activated["state"] == "active"
    state = app.meta_sentra_state()
    assert state["summary"]["active"] == 1
    assert state["active_registry"][0]["id"] == "example_county_sales"


def test_active_candidate_can_be_requarantined(tmp_path):
    app = Application(tmp_path / "clubsp.sqlite3")
    candidate = SourceCandidate(
        discovery_provider="data_gov",
        external_id="example-dataset",
        name="Example County Sales",
        source_url="https://example.gov/data.json",
        capabilities_hint=("sale_event",),
    )
    app._store_discovered_candidates("data_gov", "sales", [candidate], "c" * 64)
    fingerprint = candidate.fingerprint
    with app.database.session(write=True) as (connection, _):
        connection.execute(
            "UPDATE meta_source_candidates SET state='approved',schema_fingerprint='schema123' WHERE fingerprint=?",
            (fingerprint,),
        )
    app.meta_activate({
        "fingerprint": fingerprint,
        "sentra_id": "example_sales",
        "family": "market",
        "acquisition_mode": "official_api",
        "jurisdiction": "Example County",
        "rights_note": "Reviewed",
    })
    result = app.meta_requarantine(fingerprint, "Schema drift detected")
    assert result["state"] == "requarantined"
    state = app.meta_sentra_state()
    assert state["active_registry"] == []
