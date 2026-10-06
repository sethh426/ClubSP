from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

import pytest

from app.service import Application
from app.sentras import SENTRAS
from app.sentra_registry import SentraRegistry
from tests.sentra_helpers import activate_synthetic, synthetic_response


@pytest.mark.parametrize("values", [
    {"id": "../bad"}, {"status": "invented"}, {"source_type": "invented"},
    {"freshness_target_hours": True}, {"max_cost_per_run_cents": -1},
    {"credential_env": "actual-secret-value"}, {"source_url": "https://127.0.0.1/data"},
    {"daily_request_limit": False}, {"capabilities": ()},
    {"schema_fingerprint": "fake-schema"}, {"verified_capabilities": ("undeclared",)},
])
def test_canonical_definition_refuses_invalid_configuration(values):
    with pytest.raises(ValueError):
        replace(SENTRAS["allen_county_accdc"], **values)


def test_registry_can_route_thousands_without_cross_market_claims():
    base = SENTRAS["allen_county_accdc"]
    definitions = [replace(base, id=f"source_{i}", coverage_markets=(f"Market {i}",)) for i in range(3000)]
    registry = SentraRegistry(definitions)
    assert [d.id for d in registry.route("availability", market="Market 1800")] == ["source_1800"]
    assert not registry.route("availability", market="Unknown market")
    assert not registry.route("availability", market="Market 1800", verified_only=True)
    assert registry.revision == SentraRegistry(reversed(definitions)).revision
    with pytest.raises(ValueError, match="duplicate"):
        SentraRegistry([base, base])


def test_workspace_registry_does_not_leak_between_databases(tmp_path, monkeypatch):
    first = Application(tmp_path / "first.db")
    activate_synthetic(first, monkeypatch)
    second = Application(tmp_path / "second.db")
    assert "example_sales" in first.sentra_registry().definitions
    assert "example_sales" not in second.sentra_registry().definitions
    assert "example_sales" not in SENTRAS


def test_activation_to_execution_and_replay_survive_restart(tmp_path, monkeypatch):
    app = Application(tmp_path / "app.db")
    activate_synthetic(app, monkeypatch)
    calls = []
    def fetch(*a, **kw):
        calls.append(a[0])
        return synthetic_response()
    monkeypatch.setattr("app.meta_sentry_service.fetch_source", fetch)
    request = {"sentra_id": "example_sales", "request_key": "synthetic-first-run"}
    first = app.run_sentra(request)
    assert first["status"] == "success"
    assert first["result"]["metadata"]["evidence_scope"] == "raw_source"
    assert len(first["result"]["metadata"]["raw_sha256"]) == 64
    restarted = Application(app.database.path)
    replay = restarted.run_sentra(request)
    assert replay["replayed"] and replay["run_id"] == first["run_id"]
    assert replay["result"]["observed_at_epoch_ms"] == first["result"]["observed_at_epoch_ms"]
    assert len(calls) == 1
    entry = next(r for r in restarted.sentra_catalog_state()["sentras"] if r["id"] == "example_sales")
    assert entry["health"] == "healthy" and entry["execution_ready"]
    assert not entry["normalized_records_ready"]
    assert not restarted.state()["facts"]
    with pytest.raises(ValueError, match="different inputs"):
        restarted.run_sentra({**request, "operation": "refresh"})


def test_duplicate_concurrent_requests_consume_one_attempt(tmp_path, monkeypatch):
    app = Application(tmp_path / "app.db")
    activate_synthetic(app, monkeypatch)
    request = {"sentra_id": "example_sales", "request_key": "concurrent-test-key"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: app.run_sentra(request), range(4)))
    assert len({r["run_id"] for r in results}) == 1
    with app.database.session() as (connection, _):
        assert connection.execute("SELECT COUNT(*) FROM sentra_kernel_runs").fetchone()[0] == 1


def test_failed_attempts_use_persistent_budget_without_implicit_retry(tmp_path, monkeypatch):
    app = Application(tmp_path / "app.db")
    activate_synthetic(app, monkeypatch, daily_limit=1)
    def fail(*a, **kw):
        raise ValueError("provider response includes private material")
    monkeypatch.setattr(app, "_execute_meta_source", fail)
    first = app.run_sentra({"sentra_id": "example_sales", "request_key": "failed-attempt-one"})
    assert first["status"] == "failed" and not first["result"]
    assert "private material" not in json.dumps(first)
    app = Application(app.database.path)
    with pytest.raises(ValueError, match="budget exhausted"):
        app.run_sentra({"sentra_id": "example_sales", "request_key": "failed-attempt-two"})


def test_late_result_is_withheld_after_requarantine(tmp_path, monkeypatch):
    app = Application(tmp_path / "app.db")
    candidate = activate_synthetic(app, monkeypatch)
    def fetch(*a, **kw):
        app.meta_requarantine(candidate.fingerprint, "Synthetic in-flight source invalidation")
        return synthetic_response()
    monkeypatch.setattr("app.meta_sentry_service.fetch_source", fetch)
    result = app.run_sentra({"sentra_id": "example_sales", "request_key": "late-result-test"})
    assert result["status"] == "discarded" and not result["result"]
    assert result["error_code"] == "registry_changed_during_run"
    assert "example_sales" not in app.sentra_registry().definitions


def test_kernel_detects_nested_schema_drift_and_blocks_further_runs(tmp_path, monkeypatch):
    app = Application(tmp_path / "app.db")
    activate_synthetic(app, monkeypatch)
    monkeypatch.setattr("app.meta_sentry_service.fetch_source", lambda *a, **kw: synthetic_response({
        "features": [{"attributes": {"different_parcel_field": "SYNTHETIC"}}]}))
    result = app.run_sentra({"sentra_id": "example_sales", "request_key": "schema-drift-test"})
    assert result["status"] == "discarded" and not result["result"]
    assert app.meta_sentra_state()["candidates"][0]["state"] == "requarantined"
    with pytest.raises(ValueError, match="unknown Sentra"):
        app.run_sentra({"sentra_id": "example_sales", "request_key": "after-schema-drift"})


def test_in_progress_attempt_survives_restart_without_resubmission(tmp_path, monkeypatch):
    app = Application(tmp_path / "app.db")
    activate_synthetic(app, monkeypatch)
    request = {"sentra_id": "example_sales", "request_key": "unfinished-test-run"}
    completed = app.run_sentra(request)
    with app.database.session(write=True) as (connection, _):
        connection.execute("UPDATE sentra_kernel_runs SET status='running',result_json='{}',finished_at='' WHERE run_id=?", (completed["run_id"],))
    monkeypatch.setattr("app.meta_sentry_service.fetch_source", lambda *a, **kw: pytest.fail("Restart must not resubmit an unresolved run"))
    restarted = Application(app.database.path)
    replay = restarted.run_sentra(request)
    assert replay["replayed"] and replay["status"] == "running"


def test_reserved_actor_flag_cannot_authorize_paid_execution(tmp_path, monkeypatch):
    app = Application(tmp_path / "app.db")
    with pytest.raises(ValueError, match="implemented executor"):
        activate_synthetic(app, monkeypatch, mode="apify_actor")
    with pytest.raises(ValueError):
        app.run_sentra({"sentra_id": "example_sales", "request_key": "actor-flag-test", "input_data": {
            "approved_actor_id": "unreviewed/actor", "actor_approved": True}})


def test_reapproval_with_new_registry_id_keeps_original_run_provenance(tmp_path, monkeypatch):
    app = Application(tmp_path / "app.db")
    candidate = activate_synthetic(app, monkeypatch)
    first = app.run_sentra({"sentra_id": "example_sales", "request_key": "before-reapproval"})
    original_review = first["result"]["metadata"]["rights_review_ref"]
    app.meta_requarantine(candidate.fingerprint, "Synthetic review reset")
    app.meta_probe({"fingerprint": candidate.fingerprint})
    app.meta_propose({"fingerprint": candidate.fingerprint})
    app.meta_review({"fingerprint": candidate.fingerprint, "decision": "approve", "note": "Second synthetic review"})
    app.meta_activate({"fingerprint": candidate.fingerprint, "sentra_id": "reviewed_sales_v2",
                       "family": "parcel_assessor", "acquisition_mode": "official_api",
                       "jurisdiction": "Example County", "rights_note": "Second synthetic rights review"})
    assert "example_sales" not in app.sentra_registry().definitions
    assert app.sentra_registry().get("reviewed_sales_v2").rights_review_ref != original_review
    with app.database.session() as (connection, _):
        recorded = connection.execute("SELECT definition_json FROM sentra_kernel_runs WHERE run_id=?", (first["run_id"],)).fetchone()
    assert json.loads(recorded[0])["rights_review_ref"] == original_review
