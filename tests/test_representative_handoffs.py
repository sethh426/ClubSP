"""Representative packets preserve saved research and never send or transact."""
from datetime import datetime, timedelta, timezone
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from app.service import Application
from app.server import create_server
from tests.test_acquisition_briefs import app, transport


@pytest.fixture
def brief(app):
    app._sentra_transport = transport([])
    saved = app.build_acquisition_brief({})
    def forbidden(*args, **kwargs):
        raise AssertionError("Handoffs must not request provider data")
    app.run_sentra = forbidden
    return saved


def request(brief, **extra):
    return {"request_key": "test-handoff", "brief_id": brief["id"], "card_indices": [0],
            "client_name": "Example Client LLC", "representative_company": "Example Brokerage",
            "representative_contact": "office@example.test", "notes": "Please review only.", **extra}


def test_saved_draft_exports_known_economics_and_preserves_limits(app, brief):
    handoff = app.prepare_representative_handoff(request(brief))
    assert handoff["status"] == "draft_not_sent"
    assert handoff["representation_established"] is handoff["external_actions"] is False
    exported = app.representative_handoff_export(handoff["id"])
    for text in ("DRAFT / NOT SENT", "Example Client LLC", "Example Brokerage", "$113,000.00",
                 "6.84%", "not a representation agreement", "SOURCE REFERENCES", "Debt, income tax"):
        assert text in exported["text"]
    assert "/api/" not in exported["text"] and "synthetic-test-key" not in exported["text"]
    assert app.acquisition_brief_history()["briefs"][0]["id"] == brief["id"]


def test_same_key_is_idempotent_and_conflicting_body_is_refused(app, brief):
    first = app.prepare_representative_handoff(request(brief))
    assert app.prepare_representative_handoff(request(brief))["reused"] is True
    with pytest.raises(ValueError, match="different handoff"):
        app.prepare_representative_handoff(request(brief, client_name="Other client"))
    assert len(app.representative_handoff_history()["handoffs"]) == 1
    assert app.representative_handoff_history()["handoffs"][0]["id"] == first["id"]


@pytest.mark.parametrize("extra", [{"card_indices": []}, {"card_indices": [0, 0]}, {"card_indices": [True]},
    {"card_indices": [1]}, {"card_indices": [3]}, {"client_name": ""}, {"representative_company": ""},
    {"representative_contact": "office@example.test\nAPPROVED"}, {"notes": "x" * 2001},
    {"brief_id": "not-an-id"}, {"send": True}, {"representation_established": True}])
def test_invalid_handoff_never_saves(app, brief, extra):
    with pytest.raises(ValueError):
        app.prepare_representative_handoff(request(brief, **extra))
    assert app.representative_handoff_history()["handoffs"] == []


def test_stale_incomplete_and_below_target_evidence_stays_explicit(app, brief):
    result = brief["result"]
    result["cards"][0]["economics"] = None
    result["cards"][0]["rent_estimate"] = None
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with app.database.session(write=True) as (connection, _):
        connection.execute("UPDATE acquisition_briefs SET completed_at=?,status='incomplete',result_json=? WHERE id=?",
                           (old, json.dumps(result), brief["id"]))
    handoff = app.prepare_representative_handoff(request(brief))
    text = app.representative_handoff_export(handoff["id"])["text"]
    assert "over 24 hours old" in text and "economics remain unscored" in text
    with app.database.session(write=True) as (connection, _):
        result["cards"][0]["economics"] = {"cash_basis": 113000, "annual_operating_income": 1000, "yield_pct": .88}
        result["cards"][0]["screen"] = "below_assumed_yield"
        connection.execute("UPDATE acquisition_briefs SET result_json=? WHERE id=?", (json.dumps(result), brief["id"]))
    other = app.prepare_representative_handoff(request(brief, request_key="below"))
    assert "not as a qualifying match" in app.representative_handoff_export(other["id"])["text"]


def test_snapshot_survives_source_changes_and_application_restart(app, brief):
    first = app.prepare_representative_handoff(request(brief))
    exported = app.representative_handoff_export(first["id"])
    with app.database.session(write=True) as (connection, _):
        connection.execute("UPDATE acquisition_briefs SET result_json='{}' WHERE id=?", (brief["id"],))
    restarted = Application(app.database.path)
    assert restarted.representative_handoff_export(first["id"]) == exported
    assert restarted.representative_handoff_history()["handoffs"][0]["snapshot_sha256"] == first["snapshot_sha256"]


def test_concurrent_same_key_saves_one_packet(app, brief):
    results, errors = [], []
    def prepare():
        try:
            results.append(app.prepare_representative_handoff(request(brief)))
        except Exception as error:
            errors.append(error)
    threads = [threading.Thread(target=prepare) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert not errors and len(results) == 2 and results[0]["id"] == results[1]["id"]


def test_running_missing_and_tampered_packets_are_refused(app, brief):
    with pytest.raises(LookupError):
        app.representative_handoff_export("missing")
    with pytest.raises(ValueError, match="not found"):
        app.prepare_representative_handoff(request(brief, brief_id="00000000-0000-0000-0000-000000000000"))
    first = app.prepare_representative_handoff(request(brief))
    with app.database.session(write=True) as (connection, _):
        connection.execute("UPDATE representative_handoffs SET snapshot_json='{}' WHERE id=?", (first["id"],))
        connection.execute("UPDATE acquisition_briefs SET status='running' WHERE id=?", (brief["id"],))
    with pytest.raises(ValueError, match="integrity"):
        app.representative_handoff_export(first["id"])
    with pytest.raises(ValueError, match="finish"):
        app.prepare_representative_handoff(request(brief, request_key="new"))


def test_http_handoff_requires_origin_and_restores_export(app, brief):
    server = create_server(app.database.path, 0, application=app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    body = json.dumps(request(brief)).encode()
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base + "/api/sentras/handoffs/prepare", data=body, headers={"Content-Type": "application/json"}))
        assert error.value.code == 403
        with urlopen(Request(base + "/api/sentras/handoffs/prepare", data=body,
                     headers={"Content-Type": "application/json", "Origin": base})) as response:
            handoff = json.load(response)
        with urlopen(base + "/api/sentras/handoffs/" + handoff["id"]) as response:
            assert "Example Brokerage" in json.load(response)["text"]
        with urlopen(base + "/api/sentras/handoffs") as response:
            assert json.load(response)["handoffs"][0]["id"] == handoff["id"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
