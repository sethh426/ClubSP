import json
from dataclasses import replace

import httpx
import pytest

import app.meta_sentry_service as meta
from app.meta_sentras import SourceCandidate
from app.meta_source_transport import fetch_source
from app.schema import component_version
from app.service import Application


@pytest.fixture
def source(tmp_path, monkeypatch):
    app = Application(tmp_path / "clubsp.sqlite3")
    candidate = SourceCandidate(
        discovery_provider="data_gov", external_id="example-dataset",
        name="Example County Parcel Assessor Sales", source_url="https://data.example.gov/sales.json",
        description="parcel assessor property sales GIS", jurisdiction_hint="Example County",
        capabilities_hint=("parcel_identity", "assessment", "sale_event"),
    )
    app._store_discovered_candidates("data_gov", "parcel sales", [candidate], "a" * 64)
    fixture = {"payload": [{"parcel_id": "A-1", "sale_price": 100000, "modified_at": "2026-10-01"}],
               "status": 200, "content_type": "application/json"}
    def handle(request):
        if isinstance(fixture["payload"], bytes):
            return httpx.Response(fixture["status"], content=fixture["payload"],
                                  headers={"Content-Type": fixture["content_type"]})
        return httpx.Response(fixture["status"], json=fixture["payload"],
                              headers={"Content-Type": fixture["content_type"]})
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(meta, "fetch_source", lambda url, **kwargs: fetch_source(url, transport=transport, **kwargs))
    return app, candidate, fixture


def activate(app, candidate, *, sentra_id="example_sales", mode="official_api"):
    fingerprint = candidate.fingerprint
    app.meta_probe({"fingerprint": fingerprint})
    app.meta_propose({"fingerprint": fingerprint})
    app.meta_review({"fingerprint": fingerprint, "decision": "approve",
                     "note": "Synthetic source rights, schema and usefulness reviewed"})
    return app.meta_activate({
        "fingerprint": fingerprint, "sentra_id": sentra_id, "family": "parcel_assessor",
        "acquisition_mode": mode, "jurisdiction": "Example County",
        "rights_note": "Synthetic source; isolated test only",
    })


def test_meta_sentra_candidate_persists_and_requires_approval(source):
    app, candidate, _ = source
    state = Application(app.database.path).meta_sentra_state()
    assert state["summary"]["quarantined"] == 1
    assert state["candidates"][0]["fingerprint"] == candidate.fingerprint
    assert state["automatic_activation"] is False
    with pytest.raises(ValueError, match="explicitly approved"):
        app.meta_activate({"fingerprint": candidate.fingerprint, "sentra_id": "test",
                           "family": "market", "acquisition_mode": "official_api",
                           "jurisdiction": "Example County", "rights_note": "Test"})


def test_full_lifecycle_execution_restart_and_history(source):
    app, candidate, fixture = source
    result = activate(app, candidate)
    assert result["state"] == "active"
    restarted = Application(app.database.path)
    executed = restarted.meta_execute({"sentra_id": "example_sales"})
    assert executed["payload"] == fixture["payload"]
    assert executed["source_url"] == candidate.source_url
    assert executed["metadata"]["candidate_fingerprint"] == candidate.fingerprint
    assert executed["evidence_imported"] is False
    with restarted.database.session() as (_, memory):
        assert not memory.facts
    state = restarted.meta_sentra_state()
    assert state["summary"]["active"] == 1
    assert state["active_registry"][0]["id"] == "example_sales"
    assert state["active_registry"][0]["last_checked_at"]
    history = restarted.meta_candidate_history({"fingerprint": candidate.fingerprint})
    assert [e["to_state"] for e in history["events"]] == [
        "quarantined", "metadata_probed", "schema_probed", "proposed", "approved", "active",
    ]
    approval = next(e for e in history["events"] if e["to_state"] == "approved")
    assert approval["automated"] == 0 and approval["evidence_refs"]
    assert {c["kind"] for c in history["checks"]} == {"probe", "execution"}


@pytest.mark.parametrize("status,payload,content_type,error", [
    (401, {}, "application/json", "HTTP 401"),
    (404, {}, "application/json", "HTTP 404"),
    (302, b"", "text/html", "HTTP 302"),
    (200, b"{bad", "application/json", "invalid JSON"),
    (200, {"error": {"code": 403}}, "application/json", "provider error"),
    (200, b"<html>login</html>", "text/html", "dedicated adapter"),
    (200, b"x" * 300000, "application/json", "bounded sample"),
])
def test_failed_probe_is_recorded_and_cannot_advance(source, status, payload, content_type, error):
    app, candidate, fixture = source
    fixture.update(status=status, payload=payload, content_type=content_type)
    with pytest.raises(ValueError, match=error):
        app.meta_probe({"fingerprint": candidate.fingerprint})
    state = Application(app.database.path).meta_sentra_state()
    assert state["candidates"][0]["state"] == "quarantined"
    assert state["candidates"][0]["schema_fingerprint"] == ""
    history = app.meta_candidate_history({"fingerprint": candidate.fingerprint})
    assert len(history["events"]) == 1
    assert history["checks"][0]["status"] == "failed"
    with pytest.raises(ValueError, match="schema_probed"):
        app.meta_propose({"fingerprint": candidate.fingerprint})


def test_active_candidate_drift_is_requarantined_and_needs_new_review(source):
    app, candidate, fixture = source
    activate(app, candidate)
    fixture["payload"] = [{"parcel_id": "A-1", "price_changed_name": 90000}]
    result = app.meta_health_check({"max_sources": 1})
    assert result["requarantined"] == 1
    state = Application(app.database.path).meta_sentra_state()
    assert state["active_registry"] == []
    assert state["summary"]["requarantined"] == 1
    assert state["candidates"][0]["schema_fingerprint"] == ""
    with pytest.raises(LookupError, match="active Sentra"):
        app.meta_execute({"sentra_id": "example_sales"})
    with pytest.raises(ValueError, match="fresh schema"):
        app.meta_review({"fingerprint": candidate.fingerprint, "decision": "approve", "note": "Retry"})
    activate(app, candidate)
    assert app.meta_execute({"sentra_id": "example_sales"})["payload"] == fixture["payload"]


def test_execution_rechecks_schema_and_does_not_return_drifted_payload(source):
    app, candidate, fixture = source
    activate(app, candidate)
    fixture["payload"] = [{"parcel_id": "A-1", "sale_price": "not numeric", "modified_at": "2026-10-01"}]
    with pytest.raises(ValueError, match="re-quarantined"):
        app.meta_execute({"sentra_id": "example_sales"})
    assert app.meta_sentra_state()["active_registry"] == []


def test_source_failure_removes_active_registration(source):
    app, candidate, fixture = source
    activate(app, candidate)
    fixture["status"] = 503
    result = app.meta_health_check({"max_sources": 1})
    assert result["requarantined"] == 1
    assert app.meta_candidate_history({"fingerprint": candidate.fingerprint})["checks"][0]["status"] == "failed"


def test_health_does_not_false_alarm_on_new_values_or_nulls(source):
    app, candidate, fixture = source
    activate(app, candidate)
    fixture["payload"] = [{"parcel_id": "A-2", "sale_price": None, "modified_at": "2026-10-02"}]
    assert app.meta_health_check({"max_sources": 1})["healthy"] == 1
    assert app.meta_sentra_state()["summary"]["active"] == 1


def test_csv_executor_is_persistent_and_bounded(source):
    app, candidate, fixture = source
    fixture.update(payload=b"parcel_id,sale_price\nA-1,100000\n", content_type="text/csv")
    activate(app, candidate, mode="file_parser")
    assert app.meta_execute({"sentra_id": "example_sales"})["payload"] == [{"parcel_id": "A-1", "sale_price": "100000"}]


def test_rediscovery_deduplicates_endpoint_and_preserves_reviewed_capabilities(source):
    app, candidate, _ = source
    activate(app, candidate)
    duplicate = replace(candidate, discovery_provider="ckan", external_id="same-source",
                        capabilities_hint=("unreviewed_capability",), name="Rediscovered name")
    saved = app._store_discovered_candidates("ckan", "parcels", [duplicate], "b" * 64)
    state = Application(app.database.path).meta_sentra_state()
    assert saved["new_quarantined"] == 0 and state["summary"]["total"] == 1
    assert state["summary"]["active"] == 1
    assert "unreviewed_capability" not in state["active_registry"][0]["capabilities"]
    assert "unreviewed_capability" not in state["candidates"][0]["capabilities"]


def test_activation_refuses_unimplemented_executor(source):
    app, candidate, _ = source
    activate(app, candidate)
    with pytest.raises(ValueError, match="implemented executor"):
        app.meta_activate({"fingerprint": candidate.fingerprint, "sentra_id": "actor",
                           "acquisition_mode": "apify_actor", "family": "market",
                           "jurisdiction": "Example County", "rights_note": "Test"})


def test_execution_cannot_override_approved_source(source):
    app, candidate, _ = source
    activate(app, candidate)
    with pytest.raises(ValueError, match="only the approved"):
        app.meta_execute({"sentra_id": "example_sales", "url": "https://another.example.gov"})


def test_gap_coverage_is_market_specific(source, monkeypatch):
    app, candidate, _ = source
    activate(app, candidate)
    monkeypatch.setattr(app, "search_intents", lambda: [
        {"market": "Example County"}, {"market": "Different County"},
    ])
    plans = app.meta_gap_queries()
    different = [p for p in plans if p["market"] == "Different County" and "parcel" in p["query"]]
    assert different and "parcel_identity" in different[0]["capability_gap"]


def test_candidate_pagination_uses_total_counts(source):
    app, candidate, _ = source
    second = replace(candidate, external_id="second", source_url="https://data.example.gov/second.json")
    app._store_discovered_candidates("data_gov", "parcels", [second], "b" * 64)
    state = app.meta_sentra_state(limit=1)
    assert len(state["candidates"]) == 1 and state["summary"]["total"] == 2
    assert state["pagination"]["has_more"] is True
    assert app.meta_sentra_state(limit=1, offset=1)["pagination"]["has_more"] is False


def test_v1_upgrade_preserves_candidates_and_invalidates_legacy_activation(source):
    app, candidate, _ = source
    with app.database.session(write=True) as (connection, _):
        connection.execute("DROP TABLE meta_source_checks")
        connection.execute("DROP TABLE meta_scheduler_jobs")
        connection.execute("ALTER TABLE activated_sentras DROP COLUMN freshness_target_hours")
        connection.execute("UPDATE clubsp_schema_versions SET version=1 WHERE component='meta_sentras'")
        connection.execute("UPDATE meta_source_candidates SET state='active',schema_fingerprint='legacy' WHERE fingerprint=?", (candidate.fingerprint,))
        connection.execute("INSERT INTO activated_sentras VALUES(?,?,?,?,?,?,?,?,?,?)", (
            "legacy", candidate.fingerprint, candidate.name, "market", "playwright",
            "[]", "Example County", candidate.source_url, "Legacy", "2026-10-01",
        ))
    restarted = Application(app.database.path)
    assert restarted.meta_sentra_state()["summary"]["requarantined"] == 1
    assert restarted.meta_sentra_state()["active_registry"] == []
    with restarted.database.session() as (connection, _):
        assert component_version(connection, "meta_sentras") == 2


def test_probe_race_cannot_overwrite_rejected_candidate(source, monkeypatch):
    app, candidate, _ = source
    original = app._sample_candidate
    def racing(row):
        result = original(row)
        app.meta_review({"fingerprint": candidate.fingerprint, "decision": "reject", "note": "Do not activate"})
        return result
    monkeypatch.setattr(app, "_sample_candidate", racing)
    with pytest.raises(ValueError, match="changed during probe"):
        app.meta_probe({"fingerprint": candidate.fingerprint})
    assert app.meta_sentra_state()["candidates"][0]["state"] == "rejected"


def test_arcgis_activation_executes_bounded_records_and_monitors_layer_contract(source, monkeypatch):
    app, _, _ = source
    candidate = SourceCandidate(
        discovery_provider="arcgis_hub", external_id="layer-1", name="County Parcel Assessor GIS Sales",
        source_url="https://gis.example.gov/rest/services/Parcels/FeatureServer/0",
        description="parcel assessor GIS property sales", capabilities_hint=("parcel_identity", "assessment"),
    )
    app._store_discovered_candidates("arcgis_online", "parcels", [candidate], "d" * 64)
    requests = []
    layer = {"fields": [{"name": "parcel_id", "type": "esriFieldTypeString"},
                        {"name": "sale_price", "type": "esriFieldTypeDouble"}]}
    def handle(request):
        requests.append(request)
        assert request.url.params["f"] == "json"
        if request.url.path.endswith("/query"):
            assert request.url.params["resultRecordCount"] == "25"
            assert request.url.params["returnGeometry"] == "false"
            return httpx.Response(200, json={"features": [{"attributes": {"parcel_id": "A", "sale_price": 2000}}]})
        return httpx.Response(200, json=layer)
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(meta, "fetch_source", lambda url, **kwargs: fetch_source(url, transport=transport, **kwargs))
    activate(app, candidate, sentra_id="example_parcels", mode="arcgis")
    result = Application(app.database.path).meta_execute({"sentra_id": "example_parcels"})
    assert result["payload"]["features"][0]["attributes"]["parcel_id"] == "A"
    assert app.meta_health_check({"max_sources": 1})["healthy"] == 1
    layer["fields"][1]["type"] = "esriFieldTypeString"
    assert app.meta_health_check({"max_sources": 1})["requarantined"] == 1


def test_health_checks_rotate_fairly_across_active_sources(source):
    app, candidate, _ = source
    activate(app, candidate, sentra_id="a")
    other = replace(candidate, external_id="other", source_url="https://data.example.gov/other.json")
    app._store_discovered_candidates("data_gov", "parcels", [other], "d" * 64)
    activate(app, other, sentra_id="b")
    first = app.meta_health_check({"max_sources": 1})["results"][0]["sentra_id"]
    second = app.meta_health_check({"max_sources": 1})["results"][0]["sentra_id"]
    assert {first, second} == {"a", "b"}


def test_arcgis_service_root_discovers_only_declared_layers(source, monkeypatch):
    app, _, _ = source
    candidate = SourceCandidate(
        discovery_provider="arcgis_hub", external_id="service", name="County Parcel Assessor GIS Sales",
        source_url="https://gis.example.gov/rest/services/Parcels/FeatureServer",
        description="parcel property sales", capabilities_hint=("parcel_identity",),
    )
    app._store_discovered_candidates("arcgis_online", "parcels", [candidate], "d" * 64)
    def handle(request):
        assert request.url.params["f"] == "json"
        if request.url.path.endswith("/FeatureServer"):
            return httpx.Response(200, json={"layers": [{"id": 7, "name": "Parcels"}]})
        assert request.url.path.endswith("/7")
        return httpx.Response(200, json={"fields": [{"name": "parcel_id", "type": "esriFieldTypeString"}]})
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(meta, "fetch_source", lambda url, **kwargs: fetch_source(url, transport=transport, **kwargs))
    result = app.meta_probe({"fingerprint": candidate.fingerprint})
    assert result["state"] == "metadata_probed"
    child = result["layer_candidates"][0]
    assert child["source_url"].endswith("/7")
    with pytest.raises(ValueError, match="schema_probed"):
        app.meta_propose({"fingerprint": candidate.fingerprint})
    assert app.meta_probe({"fingerprint": child["fingerprint"]})["state"] == "schema_probed"
    assert app.meta_sentra_state()["summary"]["active"] == 0


def test_http_lifecycle_requires_owner_session_and_matching_origin(source):
    import threading
    from app.auth import OwnerAuth, SESSION_COOKIE
    from app.server import create_server
    app, candidate, _ = source
    auth = OwnerAuth(secret="synthetic-owner-secret-123456")
    server = create_server(app.database.path, port=0, application=app, auth=auth)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = "http://127.0.0.1:" + str(server.server_address[1])
    routes = ("cycle", "discover", "probe", "propose", "review", "activate", "health", "execute", "history", "requarantine")
    try:
        with httpx.Client(base_url=origin, trust_env=False) as client:
            for route in routes:
                assert client.post("/api/sentras/meta/" + route, json={}, headers={"Origin": origin}).status_code == 401
            client.cookies.set(SESSION_COOKIE, auth.issue())
            for route in routes:
                assert client.post("/api/sentras/meta/" + route, json={}).status_code == 403
                assert client.post("/api/sentras/meta/" + route, json={}, headers={"Origin": "http://foreign.invalid"}).status_code == 403
            headers = {"Origin": origin}
            fingerprint = {"fingerprint": candidate.fingerprint}
            for route, data in [
                ("probe", fingerprint), ("propose", fingerprint),
                ("review", {**fingerprint, "decision": "approve", "note": "Synthetic operator review"}),
                ("activate", {**fingerprint, "sentra_id": "http_sales", "family": "market",
                              "acquisition_mode": "official_api", "jurisdiction": "Example County", "rights_note": "Test only"}),
                ("execute", {"sentra_id": "http_sales"}), ("health", {"max_sources": 1}),
                ("history", fingerprint), ("requarantine", {**fingerprint, "reason": "Operator test"}),
            ]:
                response = client.post("/api/sentras/meta/" + route, json=data, headers=headers)
                assert response.status_code == 201, response.text
            assert client.post("/api/sentras/meta/execute", json={"sentra_id": "http_sales"}, headers=headers).status_code == 404
            assert client.get("/api/sentras/meta?limit=1").json()["summary"]["requarantined"] == 1
            assert client.get("/api/sentras/meta?limit=0").status_code == 400
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
