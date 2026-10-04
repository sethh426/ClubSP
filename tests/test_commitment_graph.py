from datetime import datetime, timedelta, timezone

from app.service import Application


def underwriting_payload():
    return {
        "property_type": "single_family", "expected_exit_price": 240000,
        "buyer_repairs": 30000, "buyer_funding_holding": 8000,
        "buyer_closing": 5000, "buyer_selling_costs": 10000,
        "buyer_minimum_profit": 42000, "target_assignment_fee": 20000,
        "owner_transaction_costs": 4000, "partner_payout_allowance": 2000,
        "contingency": 2000, "desired_owner_net": 10000,
        "basis": "Synthetic fixture only.",
    }


def setup_deal(tmp_path):
    app = Application(tmp_path / "commitment.db")
    prop = app.create_property({
        "address": "123 Synthetic St", "city": "Fort Wayne", "state": "IN", "zip": "46802",
    })
    app.record_fact({
        "property_id": prop["id"], "attribute": "property_type",
        "value": "single_family", "provider": "Synthetic fixture",
    })
    deal = app.create_deal({"property_id": prop["id"], "strategy": "assignment"})
    app.underwrite(deal["id"], underwriting_payload())
    buyer = app.create_buyer({
        "name": "Synthetic Buyer", "company": "Fixture LLC",
        "locations": ["Fort Wayne, IN"], "strategies": ["assignment"],
        "property_types": ["single_family"], "max_total_price": 160000,
        "max_repairs": 50000, "funding_status": "unverified",
    })
    app.match_buyers(deal["id"])
    return app, deal, buyer


def test_current_standing_mandate_strengthens_demand_path(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    before = app.deal_readiness(deal["id"])
    assert before["current_mandate_count"] == 0
    assert before["components"]["demand_commitment"]["score"] == 20

    now = datetime.now(timezone.utc)
    mandate = app.create_buyer_mandate({
        "buyer_id": buyer["id"],
        "name": "Fort Wayne assignment buy box",
        "markets": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "property_types": ["single_family"],
        "max_total_price": 160000,
        "max_repairs": 50000,
        "priority": 90,
        "status": "active",
        "evidence_reference": "synthetic buyer confirmation",
        "verified_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    })
    after = app.deal_readiness(deal["id"])
    assert mandate["buyer_id"] == buyer["id"]
    assert after["current_mandate_count"] == 1
    assert after["components"]["demand_commitment"]["score"] == 35
    assert after["score"] > before["score"]
    assert after["calibrated_probability"] is False


def test_capital_profile_and_outcome_are_visible_in_graph_state(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    now = datetime.now(timezone.utc)
    capital = app.create_capital_profile({
        "name": "Synthetic Funding Partner",
        "provider_type": "partner",
        "markets": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "max_commitment": 100000,
        "available_amount": 75000,
        "status": "active",
        "verification_reference": "synthetic commitment record",
        "verified_at": now.isoformat(),
        "expires_at": (now + timedelta(days=14)).isoformat(),
        "terms": {"note": "Fixture only"},
    })
    outcome = app.record_commitment_outcome({
        "deal_id": deal["id"],
        "buyer_id": buyer["id"],
        "capital_profile_id": capital["id"],
        "outcome": "buyer_declined",
        "reason_code": "price",
        "evidence_reference": "synthetic disposition note",
        "note": "Fixture outcome used to test learning evidence.",
    })
    graph = app.state()["commitment_graph"]
    assert graph["capital_profiles"][0]["available_amount"] == 75000
    assert graph["recent_outcomes"][0]["id"] == outcome["id"]
    assert graph["deal_readiness"][0]["components"]["network_outcome_evidence"]["score"] >= 2
    assert "not a calibrated closing probability" in graph["score_semantics"]


def test_expired_mandate_does_not_count_as_current(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    now = datetime.now(timezone.utc)
    app.create_buyer_mandate({
        "buyer_id": buyer["id"],
        "markets": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "property_types": [],
        "max_total_price": 160000,
        "max_repairs": 50000,
        "status": "active",
        "evidence_reference": "synthetic stale confirmation",
        "verified_at": (now - timedelta(days=60)).isoformat(),
        "expires_at": (now - timedelta(days=1)).isoformat(),
    })
    result = app.deal_readiness(deal["id"])
    assert result["current_mandate_count"] == 0
    assert any("standing mandate" in item for item in result["blockers"])
