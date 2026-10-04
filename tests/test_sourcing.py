from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from app.service import Application
from app.sourcing import sale_snapshot
from core.memory.models import utc_now
from tests.test_app import request, http_app
from tests.test_finance_operations import fixture_deal


def import_data(kind="candidates", **changes):
    data = {"kind": kind, "provider": "Synthetic authorized export", "source_url": "https://example.com/export",
            "source_date": utc_now().date().isoformat(), "rights_basis": "Synthetic fixture; no real personal data",
            "city": "Fort Wayne", "state": "IN",
            "csv": "address,zip,parcel_id,property_type\nSynthetic Intake,46802,00123,single_family"}
    if kind == "county_sales":
        data["csv"] = "Parcel Number,Address,Sale Date,Sale Price,Living Area,Property Class\n00987,Synthetic Comp," + utc_now().date().isoformat() + ',"$240,000.00",1800,Residential'
    data.update(changes)
    return data


def review_data(**changes):
    data = {"action": "accept", "reviewer": "Synthetic owner", "note": "Reviewed identity and evidence",
            "evidence_reference": "synthetic-review", "identity_confirmed": True, "sale_verified": True}
    data.update(changes)
    return data


def staged_row(app, data):
    batch = app.import_candidates(data)
    return next(r for r in app.state()["sourcing"]["rows"] if r["batch_id"] == batch["id"])


def accept_sale(app, pid, **changes):
    row = staged_row(app, import_data("county_sales", **changes))
    app.review_candidate(row["id"], review_data(property_id=pid))
    return next(s for s in app.state()["sourcing"]["sales"] if s["row_id"] == row["id"])


def add_fact(app, pid, attribute, value):
    return app.record_fact({"property_id": pid, "attribute": attribute, "value": value,
                            "provider": "Synthetic evidence", "confidence": 0.9})["fact"]


def test_preview_accept_provenance_and_restart(tmp_path):
    app = Application(tmp_path / "intake.db")
    row = staged_row(app, import_data())
    assert app.state()["properties"] == []
    assert row["value"]["parcel_id"] == "00123"
    with pytest.raises(ValueError, match="Confirm parcel"):
        app.review_candidate(row["id"], review_data(identity_confirmed=False))
    result = app.review_candidate(row["id"], review_data())
    state = Application(app.database.path).state()
    assert state["properties"][0]["id"] == result["property_id"]
    assert not state["deals"] and not state["communications"]["contacts"]
    assert state["sources"][0]["content_hash"] == state["sourcing"]["batches"][0]["raw_hash"]
    assert state["facts"][0]["observed_at"].date() == utc_now().date()
    with pytest.raises(ValueError, match="pending"):
        app.review_candidate(row["id"], review_data())


def test_idempotent_concurrent_batch_and_candidate_identity(tmp_path):
    app = Application(tmp_path / "intake.db")
    with ThreadPoolExecutor(max_workers=2) as pool:
        batches = list(pool.map(app.import_candidates, [import_data(), import_data()]))
    assert batches[0]["id"] == batches[1]["id"]
    assert sum(b["duplicate"] for b in batches) == 1
    row = app.state()["sourcing"]["rows"][0]
    app.review_candidate(row["id"], review_data())
    for csv_text in ("address,zip,parcel_id,property_type\n synthetic   INTAKE ,46802,999,single_family",
                     "address,zip,parcel_id,property_type\nOther address,46802,00123,single_family"):
        second = staged_row(app, import_data(csv=csv_text))
        with pytest.raises(ValueError, match="Existing address or parcel"):
            app.review_candidate(second["id"], review_data())
    assert len(app.state()["properties"]) == 1


def test_candidate_duplicate_checks_normalized_parcel_fact_names(tmp_path):
    app = Application(tmp_path / "intake.db")
    prop = app.create_property({"address": "Other address", "city": "Fort Wayne", "state": "IN"})
    app.record_fact({"property_id": prop["id"], "attribute": "Parcel ID", "value": "00123",
                     "provider": "Synthetic record", "confidence": 0.5})
    row = staged_row(app, import_data())
    with pytest.raises(ValueError, match="Existing address or parcel"):
        app.review_candidate(row["id"], review_data())
    assert len(app.state()["properties"]) == 1


def test_reviewed_candidates_are_ranked_as_research_opportunities_not_deals(tmp_path):
    from tests.test_opportunities import policy
    app = Application(tmp_path / "intake.db")
    row = staged_row(app, import_data())
    accepted = app.review_candidate(row["id"], review_data())
    pid = accepted["property_id"]
    add_fact(app, pid, "recorded_owner_name", "Synthetic owner")
    policy(app)
    discovery = app.state()["discovery"]
    assert discovery["execution_authorized"] is False
    assert discovery["items"][0]["decision"] == "research_candidate"
    assert discovery["items"][0]["score"] == 100
    assert discovery["items"][0]["economics_available"] is False
    assert any("seller price or terms" in reason for reason in discovery["items"][0]["reasons"])
    assert not app.state()["deals"]


def test_discovery_explains_outside_market_and_existing_pipeline(tmp_path):
    from tests.test_opportunities import policy
    app = Application(tmp_path / "intake.db")
    row = staged_row(app, import_data())
    accepted = app.review_candidate(row["id"], review_data())
    pid = accepted["property_id"]
    policy(app, markets=["Indianapolis, IN"])
    item = app.state()["discovery"]["items"][0]
    assert item["decision"] == "outside_buy_box"
    assert any("outside the saved buy box" in reason for reason in item["reasons"])
    app.create_deal({"property_id": pid, "strategy": "assignment"})
    item = app.state()["discovery"]["items"][0]
    assert item["decision"] == "already_in_pipeline"
    assert any("active deal pipeline" in reason for reason in item["reasons"])


@pytest.mark.parametrize("changes", [
    {"csv": "address,address\nA,A"}, {"csv": "address,zip,parcel_id,property_type"},
    {"csv": "address,zip,parcel_id,property_type\n" + "A,1,2,T\n" * 51},
    {"csv": "a" * 45001}, {"source_url": "http://example.com"}, {"source_url": "https://user:password@example.com"},
    {"source_date": "2099-01-01"}, {"source_date": "invalid"}, {"state": "Indiana"}, {"rights_basis": ""},
    {"kind": "invented"}])
def test_bad_batch_is_atomic(tmp_path, changes):
    app = Application(tmp_path / "intake.db")
    with pytest.raises(ValueError):
        app.import_candidates(import_data(**changes))
    assert not app.state()["sourcing"]["batches"]


@pytest.mark.parametrize("price,area,sold", [
    ("NaN", "1000", "2025-01-01"), ("0", "1000", "2025-01-01"),
    ("100.001", "1000", "2025-01-01"), ("100000", "0", "2025-01-01"),
    ("100000", "Infinity", "2025-01-01"), ("100000", "1000", "2099-01-01")])
def test_invalid_sales_never_promote(tmp_path, price, area, sold):
    app = Application(tmp_path / "intake.db")
    row = staged_row(app, import_data("county_sales", csv=f"Parcel Number,Address,Sale Date,Sale Price,Living Area\n1,A,{sold},{price},{area}"))
    assert row["status"] == "invalid" and row["errors"]
    with pytest.raises(ValueError, match="pending"):
        app.review_candidate(row["id"], review_data())


def test_bad_columns_duplicate_rows_and_exclusion_retained(tmp_path):
    app = Application(tmp_path / "intake.db")
    app.import_candidates(import_data(csv="address,zip,parcel_id,property_type\nA,1,2,T\nB,1,2,T\nC,1,3\nD,1,4,T,extra"))
    rows = sorted(app.state()["sourcing"]["rows"], key=lambda r: r["line"])
    assert [r["status"] for r in rows] == ["pending", "invalid", "invalid", "invalid"]
    app.review_candidate(rows[0]["id"], review_data(action="exclude", identity_confirmed=False))
    assert not app.state()["properties"]
    assert app.state()["sourcing"]["rows"][-1]["review"]["action"] == "exclude"


def test_sale_validity_self_comparable_and_duplicate_guards(tmp_path):
    app, did = fixture_deal(tmp_path)
    pid = app.state()["properties"][0]["id"]
    row = staged_row(app, import_data("county_sales"))
    with pytest.raises(ValueError, match="sale validity"):
        app.review_candidate(row["id"], review_data(property_id=pid, sale_verified=False))
    app.review_candidate(row["id"], review_data(property_id=pid))
    second = staged_row(app, import_data("county_sales", provider="Different source"))
    with pytest.raises(ValueError, match="already accepted"):
        app.review_candidate(second["id"], review_data(property_id=pid))
    self_row = staged_row(app, import_data("county_sales", csv="Parcel Number,Address,Sale Date,Sale Price,Living Area\n1,Synthetic 123,2025-01-01,200000,1000"))
    with pytest.raises(ValueError, match="own comparable"):
        app.review_candidate(self_row["id"], review_data(property_id=pid))
    assert len(app.state()["properties"]) == 1 and len(app.state()["deals"]) == 1


def test_changes_invalidate_queue_matching_and_contract_then_preserve_history(tmp_path):
    from tests.test_opportunities import setup_deal, item
    app, did, pid = setup_deal(tmp_path)
    assert item(app, did)["decision"] == "owner_review"
    sale = app.state()["sourcing"]["sales"][0]
    uw = app.state()["deals"][0]["underwriting"]
    app.withdraw_sale(sale["id"], review_data(note="Source corrected this transaction"))
    assert any("Comparable-sale evidence changed" in r for r in item(app, did)["reasons"])
    assert item(app, did)["current_criteria_fit_buyers"] == 0
    app.advance_deal(did, {"stage": "offer_decision", "note": "Synthetic review"})
    with pytest.raises(ValueError, match="resave underwriting"):
        app.advance_deal(did, {"stage": "contracted", "note": "Synthetic", "owner_confirmed_signed": True, "evidence_reference": "synthetic"})
    assert uw["result"]["sale_evidence"]["items"][0]["id"] == sale["id"]
    with app.database.session() as (connection, _):
        assert sale_snapshot(connection, pid)["items"] == []
    assert app.state()["sourcing"]["sales"][0]["status"] == "withdrawn"
    with pytest.raises(ValueError, match="already withdrawn"):
        app.withdraw_sale(sale["id"], review_data())


def test_old_sales_keep_sale_date_not_import_freshness(tmp_path):
    app, did = fixture_deal(tmp_path)
    pid = app.state()["properties"][0]["id"]
    accept_sale(app, pid, csv="Parcel Number,Address,Sale Date,Sale Price,Living Area\n2,Old comp,2020-01-01,200000,1000")
    app.underwrite(did, app.state()["deals"][0]["underwriting"]["inputs"])
    queue = app.state()["opportunities"]["items"][0]
    assert any("older than one year" in reason for reason in queue["reasons"])


def test_county_optional_assessor_fields_are_normalized_and_retained(tmp_path):
    app = Application(tmp_path / "intake.db")
    csv_text = ("Parcel Number,Address,Sale Date,Sale Price,Class,Acreage,Neighborhood Code,Property Code,"
                "Property Class,Year Built,Living Area,Bath,Price/SqFt,Land Value,Improvement Value,Total Value\n"
                "9,Comp,2025-01-01,240000,R,0.25,N1,P1,Residential,1980,1800,2,133.33,50000,150000,200000")
    row = staged_row(app, import_data("county_sales", csv=csv_text))
    value = row["value"]
    assert value["acreage"] == 0.25 and value["year_built"] == 1980
    assert value["bath"] == 2 and value["price_per_sqft"] == 133.33
    assert value["neighborhood_code"] == "N1" and value["property_code"] == "P1"
    assert value["land_value"] == 50000 and value["improvement_value"] == 150000
    assert value["total_value"] == 200000


def test_http_intake_review_and_withdraw_routes(http_app):
    code, body, _ = request(http_app, "/api/sourcing/import", import_data())
    assert code == 201
    code, body, _ = request(http_app, "/api/state")
    row = json.loads(body)["sourcing"]["rows"][0]
    code, body, _ = request(http_app, "/api/sourcing/rows/" + row["id"] + "/review", review_data())
    assert code == 201 and json.loads(body)["property_id"]
    code, _, _ = request(http_app, "/api/sourcing/rows/unknown/wrong", review_data())
    assert code == 404
    code, _, _ = request(http_app, "/api/sourcing/sales/unknown/withdraw", review_data())
    assert code == 404


def test_candidate_optional_price_and_property_fields_become_reviewed_evidence(tmp_path):
    app = Application(tmp_path / "intake.db")
    csv_text = (
        "address,zip,parcel_id,property_type,asking_price,beds,baths,sqft,year_built\n"
        "Synthetic Priced Intake,46802,00124,single_family,125000,3,2,1450,1988"
    )
    row = staged_row(app, import_data(csv=csv_text))
    assert row["value"]["asking_price"] == 125000
    result = app.review_candidate(row["id"], review_data())
    facts = [
        fact for fact in app.state()["facts"]
        if fact["subject_id"] == result["property_id"]
    ]
    values = {fact["attribute"]: fact["value"] for fact in facts}
    assert values["asking_price"] == 125000
    assert values["beds"] == 3
    assert values["baths"] == 2
    assert values["sqft"] == 1450
    assert values["year_built"] == 1988


@pytest.mark.parametrize("field,value", [
    ("asking_price", "NaN"),
    ("asking_price", "-1"),
    ("year_built", "1988.5"),
])
def test_invalid_optional_candidate_numeric_fields_do_not_stage_as_valid(tmp_path, field, value):
    app = Application(tmp_path / "intake.db")
    headers = "address,zip,parcel_id,property_type," + field
    csv_text = headers + "\nSynthetic Bad Optional,46802,00125,single_family," + value
    row = staged_row(app, import_data(csv=csv_text))
    assert row["status"] == "invalid"
    assert row["errors"]
