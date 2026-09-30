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


def underwriting_payload(**overrides):
    payload = {
        "property_type": "single_family", "expected_exit_price": 240000,
        "buyer_repairs": 30000, "buyer_funding_holding": 8000,
        "buyer_closing": 5000, "buyer_selling_costs": 10000,
        "buyer_minimum_profit": 42000, "target_assignment_fee": 20000,
        "owner_transaction_costs": 4000, "partner_payout_allowance": 2000,
        "contingency": 2000, "desired_owner_net": 10000,
        "basis": "Synthetic fixture; costs and exit price are owner-entered assumptions.",
    }
    payload.update(overrides)
    return payload


def test_assignment_and_resale_scenarios_reconcile(tmp_path):
    app = Application(tmp_path / "app.db")
    prop = create_property(app)
    assignment = app.create_deal({"property_id": prop["id"], "strategy": "assignment"})
    result = app.underwrite(assignment["id"], underwriting_payload())
    base = result["result"]["scenarios"]["base"]
    assert base["buyer_acquisition_ceiling"] == 145000
    assert base["owner_max_contract_price"] == 125000
    assert base["target_assignment_fee"] == 20000
    assert base["planned_owner_net"] == 12000
    assert base["profitable"] is True
    down = result["result"]["scenarios"]["downside"]
    assert down["owner_max_contract_price"] < base["owner_max_contract_price"]
    resale = app.create_deal({"property_id": prop["id"], "strategy": "resale"})
    resale_result = app.underwrite(resale["id"], underwriting_payload())
    resale_base = resale_result["result"]["scenarios"]["base"]
    assert resale_base["owner_max_contract_price"] == 169000
    assert resale_base["planned_owner_net"] == 10000


def test_deal_stages_require_evidence_and_only_follow_allowed_edges(tmp_path):
    app = Application(tmp_path / "app.db")
    prop = create_property(app)
    deal = app.create_deal({"property_id": prop["id"], "strategy": "assignment"})
    with pytest.raises(ValueError, match="Cannot move"):
        app.advance_deal(deal["id"], {"stage": "completed", "note": "skip"})
    app.advance_deal(deal["id"], {"stage": "qualified", "note": "Seller fit confirmed"})
    app.underwrite(deal["id"], underwriting_payload())
    app.advance_deal(deal["id"], {"stage": "offer_decision", "note": "Owner reviewed scenarios"})
    with pytest.raises(ValueError, match="owner confirmation"):
        app.advance_deal(deal["id"], {"stage": "contracted", "note": "Signed"})
    app.advance_deal(deal["id"], {
        "stage": "contracted", "note": "Owner confirmed", "owner_confirmed_signed": True,
        "evidence_reference": "private-doc:contract-1",
    })
    with pytest.raises(ValueError, match="cannot be added after"):
        app.underwrite(deal["id"], underwriting_payload())
    assert [event["stage_after"] for event in app.state()["deals"][0]["events"]] == [
        "qualified", "underwriting", "offer_decision", "contracted",
    ]


def test_buyer_matches_recorded_criteria_and_flags_funding_review(tmp_path):
    app = Application(tmp_path / "app.db")
    prop = create_property(app)
    app.record_fact({
        "property_id": prop["id"], "attribute": "property_type", "value": "single_family",
        "provider": "Owner review",
    })
    deal = app.create_deal({"property_id": prop["id"], "strategy": "assignment"})
    app.underwrite(deal["id"], underwriting_payload())
    buyer = app.create_buyer({
        "name": "Example Buyer", "company": "Example LLC",
        "locations": [" Fort Wayne, IN "], "strategies": ["assignment"],
        "property_types": ["single_family"], "max_total_price": 145000,
        "max_repairs": 35000, "funding_status": "unverified",
    })
    app.create_buyer({
        "name": "Out of market", "locations": ["Indianapolis, IN"],
        "strategies": ["assignment"], "property_types": [],
        "max_total_price": 200000, "max_repairs": 50000,
        "funding_status": "owner_reviewed",
    })
    matches = app.match_buyers(deal["id"])["matches"]
    candidate = next(item for item in matches if item["buyer_id"] == buyer["id"])
    assert candidate["eligible_on_recorded_criteria"] is True
    assert candidate["funding_verified_currently"] is False
    assert "funding evidence requires current owner verification" in candidate["reasons"]
    outsider = next(item for item in matches if item["name"] == "Out of market")
    assert outsider["eligible_on_recorded_criteria"] is False
    assert "market does not match" in outsider["reasons"]


def test_buyer_matching_requires_an_underwriting(tmp_path):
    app = Application(tmp_path / "app.db")
    prop = create_property(app)
    deal = app.create_deal({"property_id": prop["id"], "strategy": "resale"})
    with pytest.raises(ValueError, match="underwriting"):
        app.match_buyers(deal["id"])


def test_negative_underwriting_values_are_rejected_without_mutating_deal(tmp_path):
    app = Application(tmp_path / "app.db")
    prop = create_property(app)
    deal = app.create_deal({"property_id": prop["id"], "strategy": "resale"})
    with pytest.raises(ValueError, match="cannot be negative"):
        app.underwrite(deal["id"], underwriting_payload(buyer_repairs=-1))
    assert app.state()["deals"][0]["underwriting"] is None
    assert app.state()["deals"][0]["stage"] == "research"


def test_unprofitable_deal_cannot_be_marked_contracted(tmp_path):
    app = Application(tmp_path / "app.db")
    prop = create_property(app)
    deal = app.create_deal({"property_id": prop["id"], "strategy": "assignment"})
    app.underwrite(deal["id"], underwriting_payload(
        desired_owner_net=100000, expected_exit_price=100000,
    ))
    app.advance_deal(deal["id"], {"stage": "offer_decision", "note": "Reviewed poor case"})
    with pytest.raises(ValueError, match="profitable base underwriting"):
        app.advance_deal(deal["id"], {
            "stage": "contracted", "note": "Should remain blocked",
            "owner_confirmed_signed": True, "evidence_reference": "contract-ref",
        })
