from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import threading

import pytest

from app.database import Database, dumps
from app.server import create_server
from app.service import Application
from core.memory import SourceRecord


def create_property(app):
    return app.create_property({
        "address": "123 Example St", "city": "Fort Wayne", "state": "IN", "zip": "46802",
    })


def estimate(app, property_id):
    return app.predict({
        "property_id": property_id, "prediction_type": "repair_cost",
        "predicted_value": 30000, "confidence": 0.7,
    })


def test_full_workflow_survives_restart(tmp_path):
    path = tmp_path / "clubsp.sqlite3"
    app = Application(path)
    prop = create_property(app)
    fact = app.record_fact({
        "property_id": prop["id"], "attribute": "sqft", "value": 1800,
        "provider": "County assessor", "url": "https://example.com/parcel", "confidence": 0.9,
    })
    predicted = estimate(app, prop["id"])
    result = app.resolve(str(predicted["id"]), {
        "actual_value": 33000, "provider": "Contractor invoice", "confidence": 0.8,
    })
    restarted = Application(path).state()
    assert len(restarted["properties"]) == 1
    assert restarted["facts"][0]["source_id"] == fact["source"]["id"]
    assert restarted["predictions"][0]["actual_value"] == 33000
    assert restarted["predictions"][0]["feature_snapshot"]["fact_ids"] == [str(fact["fact"]["id"])]
    assert restarted["observations"][0]["id"] == result["observation"]["id"]
    assert restarted["learning_records"][0]["evidence_refs"] == [
        predicted["id"], result["observation"]["id"],
    ]
    assert result["learning"]["confidence"] == pytest.approx(0.8 * (1 - 3000 / 33000))


def test_failed_fact_does_not_leave_orphan_source(tmp_path):
    app = Application(tmp_path / "app.db")
    prop = create_property(app)
    with pytest.raises(ValueError):
        app.record_fact({
            "property_id": prop["id"], "attribute": "sqft",
            "value": 1800, "provider": "Manual", "confidence": 2,
        })
    assert not app.state()["sources"]
    assert not app.state()["facts"]


def test_duplicate_outcome_rolls_back_source_and_learning(tmp_path):
    app = Application(tmp_path / "app.db")
    predicted = estimate(app, create_property(app)["id"])
    payload = {"actual_value": 31000, "provider": "Invoice"}
    app.resolve(str(predicted["id"]), payload)
    before = dumps(app.state())
    with pytest.raises(ValueError, match="already been resolved"):
        app.resolve(str(predicted["id"]), payload)
    assert dumps(app.state()) == before


def test_transaction_rollback(tmp_path):
    database = Database(tmp_path / "app.db")
    with pytest.raises(RuntimeError):
        with database.session(write=True) as (_, memory):
            memory.add_source(SourceRecord(source_type="manual", provider="Test"))
            raise RuntimeError("Failure")
    with database.session() as (_, memory):
        assert not memory.sources


def test_concurrent_outcomes_create_one_learning_record(tmp_path):
    path = tmp_path / "app.db"
    app = Application(path)
    predicted = estimate(app, create_property(app)["id"])

    def resolve():
        try:
            Application(path).resolve(str(predicted["id"]), {"actual_value": 32000, "provider": "Invoice"})
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: resolve(), range(2)))
    assert sorted(results) == [False, True]
    assert len(app.state()["learning_records"]) == 1
    assert len(app.state()["sources"]) == 1


@pytest.fixture
def http_app(tmp_path):
    server = create_server(tmp_path / "http.db", port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield "http://127.0.0.1:" + str(server.server_address[1])
    server.shutdown()
    server.server_close()
    worker.join(timeout=5)


def request(base, path, data=None, headers=None):
    headers = {"Content-Type": "application/json", **(headers or {})}
    req = Request(base + path, data=dumps(data).encode() if data is not None else None, headers=headers)
    try:
        response = urlopen(req, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read(), response.headers


def test_http_end_to_end(http_app):
    code, body, headers = request(http_app, "/")
    assert code == 200
    assert b"Property Workspace" in body
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    for path in ["/app.js", "/style.css", "/api/health"]:
        assert request(http_app, path)[0] == 200
    code, body, _ = request(http_app, "/api/properties", {
        "address": "456 Test St", "city": "Fort Wayne", "state": "IN",
    })
    assert code == 201
    prop = json.loads(body)
    code, body, _ = request(http_app, "/api/facts", {
        "property_id": prop["id"], "attribute": "beds", "value": 3, "provider": "Inspection",
    })
    assert code == 201
    code, body, _ = request(http_app, "/api/predictions", {
        "property_id": prop["id"], "prediction_type": "repair_cost",
        "predicted_value": 10000, "confidence": 0.7,
    })
    assert code == 201
    prediction = json.loads(body)
    code, body, _ = request(http_app, "/api/predictions/" + prediction["id"] + "/outcome", {
        "actual_value": 12000, "provider": "Invoice",
    })
    assert code == 201
    code, body, _ = request(http_app, "/api/state")
    data = json.loads(body)
    assert len(data["facts"]) == 1
    assert len(data["learning_records"]) == 1
    assert data["predictions"][0]["resolved_at"]


def test_http_rejects_foreign_origin_and_host(http_app):
    payload = {"address": "Test", "city": "Test", "state": "IN"}
    assert request(http_app, "/api/properties", payload, {"Origin": "https://foreign.example"})[0] == 403
    assert request(http_app, "/api/state", headers={"Host": "foreign.example"})[0] == 403
    assert request(http_app, "/api/properties", payload, {"Content-Type": "text/plain"})[0] == 415
    assert request(http_app, "/../../README.md")[0] == 404


@pytest.mark.parametrize("payload", [
    {"address": "", "city": "Test", "state": "IN"},
    {"address": 12, "city": "Test", "state": "IN"},
    {"address": "Test", "city": "Test", "state": "IN", "zip": 12345},
])
def test_invalid_property_returns_readable_error(http_app, payload):
    status, body, _ = request(http_app, "/api/properties", payload)
    assert status == 400
    assert json.loads(body)["error"]
