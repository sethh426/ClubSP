from datetime import datetime, timedelta, timezone

import pytest

from app.provider_integrations import compile_rentcast_search, normalize_rentcast_listing
from tests.test_commitment_graph import setup_deal
from tests.test_sourcing import review_data


def current_intent(app, buyer_id):
    now = datetime.now(timezone.utc)
    app.create_buyer_mandate({
        "buyer_id": buyer_id,
        "name": "Provider-search mandate",
        "markets": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "property_types": ["single_family"],
        "max_total_price": 160000,
        "max_repairs": 50000,
        "priority": 90,
        "status": "active",
        "evidence_reference": "synthetic current mandate",
        "verified_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    })
    return app.search_intents()[0]


def synthetic_listing():
    return {
        "id": "synthetic-rentcast-listing-1",
        "addressLine1": "500 Synthetic Provider St",
        "city": "Fort Wayne",
        "state": "IN",
        "zipCode": "46802",
        "assessorID": "02-00-00-000-000.000-000",
        "propertyType": "Single Family",
        "bedrooms": 3,
        "bathrooms": 2,
        "squareFootage": 1450,
        "yearBuilt": 1988,
        "status": "Active",
        "price": 125000,
        "listedDate": "2026-10-01T00:00:00.000Z",
        "lastSeenDate": "2026-10-04T00:00:00.000Z",
        "daysOnMarket": 3,
    }


def test_rentcast_compiler_pushes_buyer_demand_into_provider_filters():
    intent = {
        "market": "Fort Wayne, IN",
        "property_types": ["single_family"],
        "max_total_price": 160000,
    }
    params = compile_rentcast_search(intent, max_results=10)
    assert params == {
        "city": "Fort Wayne",
        "state": "IN",
        "status": "Active",
        "limit": "10",
        "offset": "0",
        "price": "*:160000",
        "propertyType": "Single Family",
    }


def test_rentcast_normalizer_preserves_identity_and_listing_evidence():
    item = normalize_rentcast_listing(synthetic_listing())
    assert item["address"] == "500 Synthetic Provider St"
    assert item["provider_listing_id"] == "synthetic-rentcast-listing-1"
    assert item["parcel_id"] == "02-00-00-000-000.000-000"
    assert item["asking_price"] == 125000
    assert item["beds"] == 3
    assert item["baths"] == 2
    assert item["sqft"] == 1450
    assert item["year_built"] == 1988


def test_explicit_provider_search_stages_candidates_for_review(tmp_path, monkeypatch):
    app, deal, buyer = setup_deal(tmp_path)
    intent = current_intent(app, buyer["id"])
    monkeypatch.setenv("RENTCAST_API_KEY", "synthetic-key")
    monkeypatch.setenv("CLUBSP_RENTCAST_MONTHLY_REQUEST_CAP", "2")
    calls = []

    def fake_fetch(provider, params, api_key):
        calls.append((provider["id"], dict(params), api_key))
        return [synthetic_listing()], "https://api.rentcast.io/v1/listings/sale?synthetic=1"

    app._provider_fetch = fake_fetch
    result = app.search_property_provider({
        "provider_id": "rentcast",
        "search_intent_id": intent["intent_id"],
        "max_results": 10,
        "confirm_paid_request": True,
    })
    assert result["status"] == "success"
    assert result["result_count"] == 1
    assert result["creates_deals"] is False
    assert result["requires_candidate_review"] is True
    assert calls[0][0] == "rentcast"
    assert calls[0][1]["price"] == "*:160000"
    state = app.state()
    assert not any(d["property_id"] == "synthetic-rentcast-listing-1" for d in state["deals"])
    batch = next(x for x in state["sourcing"]["batches"] if x["id"] == result["batch_id"])
    assert batch["provider"] == "RentCast"
    row = next(x for x in state["sourcing"]["rows"] if x["batch_id"] == result["batch_id"])
    assert row["status"] == "pending"
    assert row["value"]["asking_price"] == 125000
    provider = state["provider_integrations"]["providers"][0]
    assert provider["usage"]["attempted_requests"] == 1
    assert provider["remaining_local_requests"] == 1


def test_provider_search_requires_per_request_confirmation(tmp_path, monkeypatch):
    app, deal, buyer = setup_deal(tmp_path)
    intent = current_intent(app, buyer["id"])
    monkeypatch.setenv("RENTCAST_API_KEY", "synthetic-key")
    called = False

    def fake_fetch(provider, params, api_key):
        nonlocal called
        called = True
        return [], provider["endpoint"]

    app._provider_fetch = fake_fetch
    with pytest.raises(ValueError, match="confirm_paid_request"):
        app.search_property_provider({
            "provider_id": "rentcast",
            "search_intent_id": intent["intent_id"],
            "max_results": 10,
            "confirm_paid_request": False,
        })
    assert called is False


def test_local_monthly_cap_blocks_before_external_call(tmp_path, monkeypatch):
    app, deal, buyer = setup_deal(tmp_path)
    intent = current_intent(app, buyer["id"])
    monkeypatch.setenv("RENTCAST_API_KEY", "synthetic-key")
    monkeypatch.setenv("CLUBSP_RENTCAST_MONTHLY_REQUEST_CAP", "1")
    calls = 0

    def fake_fetch(provider, params, api_key):
        nonlocal calls
        calls += 1
        return [synthetic_listing()], provider["endpoint"]

    app._provider_fetch = fake_fetch
    payload = {
        "provider_id": "rentcast",
        "search_intent_id": intent["intent_id"],
        "max_results": 5,
        "confirm_paid_request": True,
    }
    app.search_property_provider(payload)
    with pytest.raises(ValueError, match="Local monthly provider request cap"):
        app.search_property_provider({**payload, "force_refresh": True})
    assert calls == 1


def test_rentcast_compiler_includes_optional_mandate_ranges():
    intent = {
        "market": "Fort Wayne, IN",
        "property_types": ["single_family"],
        "max_total_price": 160000,
        "filters": {
            "min_beds": 3,
            "max_baths": 2.5,
            "min_sqft": 1200,
            "max_sqft": 2200,
            "min_year_built": 1970,
        },
    }
    params = compile_rentcast_search(intent, max_results=20)
    assert params["bedrooms"] == "3:*"
    assert params["bathrooms"] == "*:2.5"
    assert params["squareFootage"] == "1200:2200"
    assert params["yearBuilt"] == "1970:*"


def test_auto_router_selects_configured_capable_provider(tmp_path, monkeypatch):
    app, deal, buyer = setup_deal(tmp_path)
    now = datetime.now(timezone.utc)
    app.create_buyer_mandate({
        "buyer_id": buyer["id"],
        "name": "Rich provider-search mandate",
        "markets": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "property_types": ["single_family"],
        "max_total_price": 160000,
        "max_repairs": 50000,
        "min_beds": 3,
        "max_baths": 3,
        "min_sqft": 1200,
        "min_year_built": 1960,
        "priority": 95,
        "status": "active",
        "evidence_reference": "synthetic rich mandate",
        "verified_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    })
    monkeypatch.setenv("RENTCAST_API_KEY", "synthetic-key")
    intent = next(x for x in app.search_intents() if x["filters"].get("min_beds") == 3)
    route = app.route_property_provider(intent)
    assert route["selected_provider_id"] == "rentcast"
    assert route["required_capabilities"] == [
        "baths", "beds", "market", "max_price", "property_type", "sqft", "year_built"
    ]


def test_auto_provider_search_uses_router(tmp_path, monkeypatch):
    app, deal, buyer = setup_deal(tmp_path)
    intent = current_intent(app, buyer["id"])
    monkeypatch.setenv("RENTCAST_API_KEY", "synthetic-key")
    app._provider_fetch = lambda provider, params, api_key: ([synthetic_listing()], provider["endpoint"])
    result = app.search_property_provider({
        "provider_id": "auto",
        "search_intent_id": intent["intent_id"],
        "max_results": 10,
        "confirm_paid_request": True,
    })
    assert result["provider_id"] == "rentcast"
    assert result["status"] == "success"


def test_identical_fresh_provider_search_reuses_cached_run_without_second_call(tmp_path, monkeypatch):
    app, deal, buyer = setup_deal(tmp_path)
    intent = current_intent(app, buyer["id"])
    monkeypatch.setenv("RENTCAST_API_KEY", "synthetic-key")
    calls = 0

    def fake_fetch(provider, params, api_key):
        nonlocal calls
        calls += 1
        return [synthetic_listing()], provider["endpoint"]

    app._provider_fetch = fake_fetch
    payload = {
        "provider_id": "auto",
        "search_intent_id": intent["intent_id"],
        "max_results": 10,
        "confirm_paid_request": True,
    }
    first = app.search_property_provider(payload)
    second = app.search_property_provider(payload)
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["id"] == first["id"]
    assert second["batch_id"] == first["batch_id"]
    assert calls == 1
    provider = app.state()["provider_integrations"]["providers"][0]
    assert provider["usage"]["attempted_requests"] == 1


def test_force_refresh_bypasses_fresh_search_cache(tmp_path, monkeypatch):
    app, deal, buyer = setup_deal(tmp_path)
    intent = current_intent(app, buyer["id"])
    monkeypatch.setenv("RENTCAST_API_KEY", "synthetic-key")
    monkeypatch.setenv("CLUBSP_RENTCAST_MONTHLY_REQUEST_CAP", "3")
    calls = 0

    def fake_fetch(provider, params, api_key):
        nonlocal calls
        calls += 1
        row = synthetic_listing()
        row["id"] = f"synthetic-{calls}"
        return [row], provider["endpoint"]

    app._provider_fetch = fake_fetch
    payload = {
        "provider_id": "auto",
        "search_intent_id": intent["intent_id"],
        "max_results": 10,
        "confirm_paid_request": True,
    }
    app.search_property_provider(payload)
    refreshed = app.search_property_provider({**payload, "force_refresh": True})
    assert refreshed["cached"] is False
    assert calls == 2


def test_identical_buyer_demand_is_pooled_into_one_provider_request(tmp_path, monkeypatch):
    app, deal, buyer = setup_deal(tmp_path)
    first = current_intent(app, buyer["id"])
    second_buyer = app.create_buyer({
        "name": "Synthetic Buyer Two",
        "company": "Fixture Two LLC",
        "locations": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "property_types": ["single_family"],
        "max_total_price": 160000,
        "max_repairs": 50000,
        "funding_status": "unverified",
    })
    second = current_intent(app, second_buyer["id"])
    monkeypatch.setenv("RENTCAST_API_KEY", "synthetic-key")
    calls = 0

    def fake_fetch(provider, params, api_key):
        nonlocal calls
        calls += 1
        return [synthetic_listing()], provider["endpoint"]

    app._provider_fetch = fake_fetch
    result = app.search_property_provider({
        "provider_id": "auto",
        "search_intent_id": first["intent_id"],
        "max_results": 10,
        "confirm_paid_request": True,
    })
    assert calls == 1
    assert result["shared_demand"] is True
    assert result["linked_intent_count"] >= 2
    with app.database.session() as (connection, _):
        links = connection.execute(
            "SELECT search_intent_id,buyer_id FROM provider_search_links WHERE run_id=?",
            (result["id"],),
        ).fetchall()
        assert {row["buyer_id"] for row in links} >= {buyer["id"], second_buyer["id"]}
        batch = connection.execute("SELECT body FROM sourcing_batches WHERE id=?", (result["batch_id"],)).fetchone()
        body = __import__("json").loads(batch["body"])
        assert first["intent_id"] in body["linked_search_intent_ids"]
        assert second["intent_id"] in body["linked_search_intent_ids"]


@pytest.mark.parametrize("field,value", [
    ("price", float("nan")),
    ("bedrooms", -1),
    ("bathrooms", float("inf")),
    ("squareFootage", 20_000_000),
    ("yearBuilt", 9999),
])
def test_provider_normalizer_rejects_malformed_numeric_listing_data(field, value):
    row = synthetic_listing()
    row[field] = value
    with pytest.raises(ValueError):
        normalize_rentcast_listing(row)


def test_provider_normalizer_rejects_invalid_zip_and_oversized_identity():
    row = synthetic_listing()
    row["zipCode"] = "not-a-zip"
    with pytest.raises(ValueError):
        normalize_rentcast_listing(row)
    row = synthetic_listing()
    row["id"] = "x" * 201
    with pytest.raises(ValueError):
        normalize_rentcast_listing(row)


def test_provider_metrics_follow_reviewed_candidate_into_recorded_close(tmp_path, monkeypatch):
    app, deal, buyer = setup_deal(tmp_path)
    intent = current_intent(app, buyer["id"])
    monkeypatch.setenv("RENTCAST_API_KEY", "synthetic-key")
    app._provider_fetch = lambda provider, params, api_key: ([synthetic_listing()], provider["endpoint"])
    result = app.search_property_provider({
        "provider_id": "auto",
        "search_intent_id": intent["intent_id"],
        "max_results": 10,
        "confirm_paid_request": True,
    })
    row = next(x for x in app.state()["sourcing"]["rows"] if x["batch_id"] == result["batch_id"])
    accepted = app.review_candidate(row["id"], review_data())
    sourced_deal = app.create_deal({"property_id": accepted["property_id"], "strategy": "assignment"})
    app.record_commitment_outcome({
        "deal_id": sourced_deal["id"],
        "outcome": "closed",
        "reason_code": "settled",
        "evidence_reference": "synthetic provider-origin close",
        "note": "fixture",
    })
    metrics = app.state()["provider_integrations"]["providers"][0]["review_metrics"]
    assert metrics["reviewed_candidates"] == 1
    assert metrics["accepted_candidates"] == 1
    assert metrics["deals_created"] == 1
    assert metrics["closed_deals"] == 1
    assert metrics["closed_per_reviewed_candidate"] == 1.0
