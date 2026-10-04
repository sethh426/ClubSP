from datetime import datetime, timedelta, timezone

import pytest

from app.provider_integrations import compile_rentcast_search, normalize_rentcast_listing
from tests.test_commitment_graph import setup_deal


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
        app.search_property_provider(payload)
    assert calls == 1
