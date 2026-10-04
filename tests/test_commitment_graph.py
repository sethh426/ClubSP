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


def test_reverse_candidate_matching_prefers_current_standing_demand(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    now = datetime.now(timezone.utc)
    app.create_buyer_mandate({
        "buyer_id": buyer["id"],
        "name": "Buyer-first search mandate",
        "markets": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "property_types": ["single_family"],
        "max_total_price": 160000,
        "max_repairs": 50000,
        "priority": 100,
        "status": "active",
        "evidence_reference": "synthetic current mandate",
        "verified_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    })
    with app.database.session() as (connection, _):
        matches = app.reverse_match_candidate(
            connection,
            market="Fort Wayne, IN",
            property_type="single_family",
            asking_price=125000,
        )
        outside = app.reverse_match_candidate(
            connection,
            market="Indianapolis, IN",
            property_type="single_family",
            asking_price=125000,
        )
        overpriced = app.reverse_match_candidate(
            connection,
            market="Fort Wayne, IN",
            property_type="single_family",
            asking_price=175000,
        )
    assert matches[0]["buyer_id"] == buyer["id"]
    assert matches[0]["score"] == 100
    assert outside == []
    assert overpriced == []


def test_commitment_status_changes_are_audited_and_remove_current_path(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    now = datetime.now(timezone.utc)
    mandate = app.create_buyer_mandate({
        "buyer_id": buyer["id"],
        "markets": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "property_types": ["single_family"],
        "max_total_price": 160000,
        "max_repairs": 50000,
        "status": "active",
        "evidence_reference": "synthetic confirmation",
        "verified_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    })
    assert app.deal_readiness(deal["id"])["current_mandate_count"] == 1
    event = app.change_commitment_status("buyer_mandate", mandate["id"], {
        "status": "paused",
        "note": "Synthetic buyer paused acquisitions",
        "evidence_reference": "synthetic pause record",
    })
    assert event["status_before"] == "active"
    assert event["status_after"] == "paused"
    graph = app.state()["commitment_graph"]
    assert graph["buyer_mandates"][0]["status"] == "paused"
    assert graph["recent_events"][0]["entity_id"] == mandate["id"]
    assert graph["deal_readiness"][0]["current_mandate_count"] == 0


def test_readiness_invalidates_stale_buyer_match_after_criteria_change(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    before = app.deal_readiness(deal["id"])
    assert before["buyer_matches_current"] is True
    assert before["stale_buyer_match_count"] == 0
    with app.database.session(write=True) as (connection, _):
        connection.execute(
            "UPDATE buyers SET max_total_price=? WHERE id=?",
            (1000, buyer["id"]),
        )
    after = app.deal_readiness(deal["id"])
    assert after["buyer_matches_current"] is False
    assert after["stale_buyer_match_count"] >= 1
    assert after["eligible_buyer_count"] == 0
    assert any("Buyer-match evidence is stale" in item for item in after["blockers"])
    assert any("Re-run buyer matching" in item for item in after["next_actions"])


def test_reliability_summaries_are_descriptive_not_predictive(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    now = datetime.now(timezone.utc)
    capital = app.create_capital_profile({
        "name": "Synthetic Reliability Capital",
        "provider_type": "partner",
        "markets": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "max_commitment": 100000,
        "available_amount": 100000,
        "status": "active",
        "verification_reference": "synthetic capital evidence",
        "verified_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
        "terms": {},
    })
    for outcome, reason in (("closed", "settled"), ("buyer_declined", "price")):
        app.record_commitment_outcome({
            "deal_id": deal["id"],
            "buyer_id": buyer["id"],
            "capital_profile_id": capital["id"],
            "outcome": outcome,
            "reason_code": reason,
            "evidence_reference": "synthetic outcome evidence",
            "note": "fixture",
        })
    graph = app.state()["commitment_graph"]
    buyer_summary = next(x for x in graph["buyer_reliability"] if x["buyer_id"] == buyer["id"])
    capital_summary = next(x for x in graph["capital_reliability"] if x["capital_profile_id"] == capital["id"])
    assert buyer_summary["recorded_outcomes"] == 2
    assert buyer_summary["descriptive_close_rate"] == 0.5
    assert buyer_summary["calibrated_probability"] is False
    assert capital_summary["recorded_outcomes"] == 2
    assert capital_summary["descriptive_close_rate"] == 0.5
    assert capital_summary["calibrated_probability"] is False


def test_active_mandates_generate_provider_neutral_search_intents(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    now = datetime.now(timezone.utc)
    app.create_buyer_mandate({
        "buyer_id": buyer["id"],
        "name": "Demand-first intent",
        "markets": ["Fort Wayne, IN", "New Haven, IN"],
        "strategies": ["assignment"],
        "property_types": ["single_family"],
        "max_total_price": 160000,
        "max_repairs": 50000,
        "priority": 80,
        "status": "active",
        "evidence_reference": "synthetic intent evidence",
        "verified_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    })
    intents = app.state()["commitment_graph"]["search_intents"]
    assert len(intents) == 2
    assert {x["market"] for x in intents} == {"Fort Wayne, IN", "New Haven, IN"}
    assert all(x["buyer_id"] == buyer["id"] for x in intents)
    assert all(x["purpose"] == "provider-neutral demand-first sourcing input" for x in intents)


def test_outcome_can_preserve_exact_mandate_and_match_snapshot(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    now = datetime.now(timezone.utc)
    mandate = app.create_buyer_mandate({
        "buyer_id": buyer["id"],
        "name": "Exact outcome mandate",
        "markets": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "property_types": ["single_family"],
        "max_total_price": 160000,
        "max_repairs": 50000,
        "priority": 95,
        "status": "active",
        "evidence_reference": "synthetic mandate evidence",
        "verified_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    })
    state = app.state()
    match_run_id = state["deals"][0]["buyer_matches"]["id"]
    outcome = app.record_commitment_outcome({
        "deal_id": deal["id"],
        "buyer_id": buyer["id"],
        "buyer_mandate_id": mandate["id"],
        "buyer_match_run_id": match_run_id,
        "outcome": "closed",
        "reason_code": "settled",
        "evidence_reference": "synthetic settlement evidence",
        "note": "fixture",
    })
    assert outcome["buyer_mandate_id"] == mandate["id"]
    with app.database.session() as (connection, _):
        row = connection.execute(
            "SELECT * FROM commitment_outcome_context WHERE outcome_id=?",
            (outcome["id"],),
        ).fetchone()
        assert row is not None
        assert row["buyer_mandate_id"] == mandate["id"]
        assert row["buyer_match_run_id"] == match_run_id
    summary = next(
        x for x in app.state()["commitment_graph"]["mandate_reliability"]
        if x["buyer_mandate_id"] == mandate["id"]
    )
    assert summary["recorded_outcomes"] == 1
    assert summary["closed_outcomes"] == 1
    assert summary["descriptive_close_rate"] == 1.0
    assert summary["calibrated_probability"] is False


def test_mandate_property_ranges_flow_into_search_intents_and_reverse_match(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    now = datetime.now(timezone.utc)
    mandate = app.create_buyer_mandate({
        "buyer_id": buyer["id"],
        "name": "3-bed target",
        "markets": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "property_types": ["single_family"],
        "max_total_price": 160000,
        "max_repairs": 50000,
        "min_beds": 3,
        "max_beds": 4,
        "min_sqft": 1200,
        "max_sqft": 2200,
        "priority": 90,
        "status": "active",
        "evidence_reference": "synthetic range evidence",
        "verified_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    })
    intent = next(x for x in app.search_intents() if x["mandate_id"] == mandate["id"])
    assert intent["filters"]["min_beds"] == 3
    assert intent["filters"]["max_sqft"] == 2200
    with app.database.session() as (connection, _):
        good = app.reverse_match_candidate(
            connection, market="Fort Wayne, IN", property_type="single_family",
            asking_price=125000, beds=3, sqft=1500,
        )
        bad = app.reverse_match_candidate(
            connection, market="Fort Wayne, IN", property_type="single_family",
            asking_price=125000, beds=2, sqft=1500,
        )
    assert any(x["mandate_id"] == mandate["id"] for x in good)
    assert not any(x["mandate_id"] == mandate["id"] for x in bad)


def test_invalid_mandate_range_is_rejected(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    now = datetime.now(timezone.utc)
    import pytest
    with pytest.raises(ValueError, match="min_beds cannot exceed max_beds"):
        app.create_buyer_mandate({
            "buyer_id": buyer["id"],
            "markets": ["Fort Wayne, IN"],
            "strategies": ["assignment"],
            "property_types": ["single_family"],
            "max_total_price": 160000,
            "max_repairs": 50000,
            "min_beds": 5,
            "max_beds": 2,
            "status": "active",
            "evidence_reference": "synthetic invalid range",
            "verified_at": now.isoformat(),
        })


def test_stale_mandate_is_excluded_until_reconfirmed(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    old = datetime.now(timezone.utc) - timedelta(days=91)
    mandate = app.create_buyer_mandate({
        "buyer_id": buyer["id"],
        "name": "Stale demand",
        "markets": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "property_types": ["single_family"],
        "max_total_price": 160000,
        "max_repairs": 50000,
        "status": "active",
        "evidence_reference": "old synthetic confirmation",
        "verified_at": old.isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
    })
    assert not any(x["mandate_id"] == mandate["id"] for x in app.search_intents())
    with app.database.session() as (connection, _):
        assert not any(
            x["mandate_id"] == mandate["id"]
            for x in app.reverse_match_candidate(
                connection,
                market="Fort Wayne, IN",
                property_type="single_family",
                asking_price=125000,
            )
        )
    event = app.reconfirm_commitment("buyer_mandate", mandate["id"], {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "evidence_reference": "new synthetic confirmation",
        "note": "Buyer reconfirmed the buy box.",
    })
    assert event["status_before"] == "active"
    assert event["status_after"] == "active"
    assert any(x["mandate_id"] == mandate["id"] for x in app.search_intents())
    graph = app.state()["commitment_graph"]
    assert graph["recent_events"][0]["entity_id"] == mandate["id"]


def test_stale_capital_is_excluded_from_readiness_until_reconfirmed(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    app.save_financial_plan(deal["id"], {
        "seller_price": 100000,
        "assignment_fee": 10000,
        "planned_cash_at_risk": 25000,
        "max_cash_at_risk": 30000,
        "basis": "Synthetic plan for capital freshness.",
    })
    old = datetime.now(timezone.utc) - timedelta(days=31)
    capital = app.create_capital_profile({
        "name": "Stale Synthetic Capital",
        "provider_type": "partner",
        "markets": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "max_commitment": 100000,
        "available_amount": 100000,
        "status": "active",
        "verification_reference": "old synthetic capital confirmation",
        "verified_at": old.isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "terms": {},
    })
    before = app.deal_readiness(deal["id"])
    assert before["components"]["capital_path"]["score"] == 0
    app.reconfirm_commitment("capital_profile", capital["id"], {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "evidence_reference": "new synthetic capital confirmation",
        "note": "Capital partner reconfirmed available funds.",
    })
    after = app.deal_readiness(deal["id"])
    assert after["components"]["capital_path"]["score"] == 20


def test_buyer_commitment_reservation_consumes_and_releases_demand_slot(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    now = datetime.now(timezone.utc)
    mandate = app.create_buyer_mandate({
        "buyer_id": buyer["id"],
        "name": "One-slot commitment",
        "markets": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "property_types": ["single_family"],
        "max_total_price": 160000,
        "max_repairs": 50000,
        "max_active_reservations": 1,
        "target_units_per_month": 3,
        "priority": 95,
        "status": "active",
        "evidence_reference": "synthetic buyer reservation confirmation",
        "verified_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    })
    assert any(x["mandate_id"] == mandate["id"] for x in app.search_intents())
    reservation = app.reserve_buyer_commitment({
        "deal_id": deal["id"],
        "mandate_id": mandate["id"],
        "expires_at": (now + timedelta(days=7)).isoformat(),
        "evidence_reference": "synthetic buyer hold",
        "note": "Buyer reserved one acquisition slot.",
    })
    graph = app.state()["commitment_graph"]
    saved = next(x for x in graph["buyer_mandates"] if x["id"] == mandate["id"])
    assert saved["active_reservations"] == 1
    assert saved["available_reservation_slots"] == 0
    assert saved["target_units_per_month"] == 3
    assert not any(x["mandate_id"] == mandate["id"] for x in graph["search_intents"])
    readiness = next(x for x in graph["deal_readiness"] if x["deal_id"] == deal["id"])
    assert readiness["active_reservation"]["id"] == reservation["id"]

    released = app.release_buyer_commitment(reservation["id"], {
        "evidence_reference": "synthetic release evidence",
        "note": "Buyer slot released after deal review.",
    })
    assert released["status"] == "released"
    graph = app.state()["commitment_graph"]
    saved = next(x for x in graph["buyer_mandates"] if x["id"] == mandate["id"])
    assert saved["active_reservations"] == 0
    assert saved["available_reservation_slots"] == 1
    assert any(x["mandate_id"] == mandate["id"] for x in graph["search_intents"])


def test_reservation_rejects_overbooking_same_mandate(tmp_path):
    app, deal, buyer = setup_deal(tmp_path)
    second_prop = app.create_property({
        "address": "456 Synthetic Reserve St", "city": "Fort Wayne", "state": "IN", "zip": "46802",
    })
    app.record_fact({
        "property_id": second_prop["id"], "attribute": "property_type",
        "value": "single_family", "provider": "Synthetic fixture",
    })
    second_deal = app.create_deal({"property_id": second_prop["id"], "strategy": "assignment"})
    app.underwrite(second_deal["id"], underwriting_payload())
    app.match_buyers(second_deal["id"])
    now = datetime.now(timezone.utc)
    mandate = app.create_buyer_mandate({
        "buyer_id": buyer["id"],
        "markets": ["Fort Wayne, IN"],
        "strategies": ["assignment"],
        "property_types": ["single_family"],
        "max_total_price": 160000,
        "max_repairs": 50000,
        "max_active_reservations": 1,
        "target_units_per_month": 1,
        "status": "active",
        "evidence_reference": "synthetic one-slot commitment",
        "verified_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    })
    app.reserve_buyer_commitment({
        "deal_id": deal["id"], "mandate_id": mandate["id"],
        "expires_at": (now + timedelta(days=7)).isoformat(),
        "evidence_reference": "synthetic first hold",
    })
    import pytest
    with pytest.raises(ValueError, match="no available reservation slots"):
        app.reserve_buyer_commitment({
            "deal_id": second_deal["id"], "mandate_id": mandate["id"],
            "expires_at": (now + timedelta(days=7)).isoformat(),
            "evidence_reference": "synthetic overbook attempt",
        })
