from datetime import timedelta
import json
from uuid import UUID

import pytest

from app.opportunities import property_evidence
from core.memory.models import utc_now
from tests.test_app import http_app, request
from tests.test_finance_operations import fixture_deal, plan, clear_tasks, cash


def policy(app, **changes):
    data = {"markets": ["Fort Wayne, IN"], "strategies": ["assignment"],
            "property_types": ["single_family"], "max_seller_price": 150000,
            "max_deal_cash_at_risk": 10000, "max_portfolio_cash_at_risk": 20000,
            "min_downside_net": 0, "evidence_max_age_days": 30, "basis": "Synthetic review policy"}
    data.update(changes)
    return app.save_opportunity_policy(data)


def fact(app, pid, attribute, value, **changes):
    return app.record_fact({"property_id": pid, "attribute": attribute, "value": value,
                            "provider": "Synthetic evidence", "confidence": 0.9, **changes})["fact"]


def item(app, did):
    return next(i for i in app.state()["opportunities"]["items"] if i["deal_id"] == did)


def setup_deal(tmp_path):
    app, did = fixture_deal(tmp_path)
    pid = app.state()["deals"][0]["property_id"]
    fact(app, pid, "property_type", "single_family")
    fact(app, pid, "recorded_owner_name", "Synthetic Owner")
    app.underwrite(did, app.state()["deals"][0]["underwriting"]["inputs"])
    plan(app, did, seller_price=80000)
    app.create_buyer({"name": "Synthetic buyer", "locations": ["Fort Wayne, IN"],
                      "strategies": ["assignment"], "property_types": ["single_family"],
                      "max_total_price": 160000, "max_repairs": 40000,
                      "funding_status": "verified", "verified_at": utc_now().isoformat(),
                      "verification_reference": "Synthetic owner review, not independent verification"})
    clear_tasks(app, did, "contracted")
    policy(app)
    return app, did, pid


def test_queue_is_read_only_and_policy_survives_restart(tmp_path):
    app, did, _ = setup_deal(tmp_path)
    with app.database.session() as (connection, _):
        before = connection.total_changes
        assert connection.execute("SELECT count(*) FROM buyer_match_runs").fetchone()[0] == 0
    first = item(app, did)
    assert first["decision"] == "owner_review"
    assert first["current_criteria_fit_buyers"] == 1
    assert first == item(app, did)
    assert app.state()["opportunities"]["execution_authorized"] is False
    with app.database.session() as (connection, _):
        assert connection.total_changes == before == 0
        assert connection.execute("SELECT count(*) FROM buyer_match_runs").fetchone()[0] == 0
    from app.service import Application
    assert Application(app.database.path).state()["opportunities"]["policy"]["markets"] == ["fort wayne, in"]


def test_missing_setup_is_unknown_not_profitable(tmp_path):
    app, did = fixture_deal(tmp_path)
    result = item(app, did)
    assert result["decision"] == "research"
    assert result["economics"] is None
    assert "Save a buy box" in result["next_action"]
    assert any("owner-of-record" in reason for reason in result["reasons"])


def test_conflicting_types_block_all_buyers_and_supersession_clears_conflict(tmp_path):
    app, did, pid = setup_deal(tmp_path)
    conflict = fact(app, pid, "property type", "duplex")
    result = item(app, did)
    assert result["decision"] == "blocked"
    assert result["conflicts"] == ["property_type"]
    assert result["current_criteria_fit_buyers"] == 0
    assert not app.match_buyers(did)["matches"][0]["eligible_on_recorded_criteria"]
    fact(app, pid, "property type", "single family", supersedes_fact_id=str(conflict["id"]))
    assert item(app, did)["conflicts"] == []
    assert item(app, did)["decision"] == "research"  # New facts invalidate underwriting.


def test_matching_ignores_superseded_and_nonactive_types(tmp_path):
    app, did, pid = setup_deal(tmp_path)
    old = fact(app, pid, "property_type", "duplex")
    fact(app, pid, "property_type", "single_family", supersedes_fact_id=str(old["id"]))
    with app.database.session(write=True) as (_, memory):
        memory.facts[UUID(old["id"]) if isinstance(old["id"], str) else old["id"]].status = "disputed"
    app.underwrite(did, app.state()["deals"][0]["underwriting"]["inputs"])
    plan(app, did, seller_price=80000)
    result = app.match_buyers(did)["matches"][0]
    assert result["eligible_on_recorded_criteria"] is True
    with app.database.session() as (connection, _):
        evidence = property_evidence(connection, pid)
    assert str(old["id"]) not in [f["id"] for f in evidence["facts"]]


def test_any_fact_change_invalidates_underwriting_and_live_matches(tmp_path):
    app, did, pid = setup_deal(tmp_path)
    fact(app, pid, "roof_condition", "Needs inspection")
    assert any("Underwriting evidence changed" in r for r in item(app, did)["reasons"])
    assert item(app, did)["current_criteria_fit_buyers"] == 0
    assert not app.match_buyers(did)["matches"][0]["eligible_on_recorded_criteria"]
    app.underwrite(did, app.state()["deals"][0]["underwriting"]["inputs"])
    assert "Financial plan must reference the latest underwriting" in item(app, did)["reasons"]
    plan(app, did, seller_price=80000)
    assert item(app, did)["decision"] == "owner_review"


def test_legacy_underwriting_requires_explicit_review(tmp_path):
    app, did, _ = setup_deal(tmp_path)
    with app.database.session(write=True) as (connection, _):
        row = connection.execute("SELECT id,result_json FROM underwritings WHERE deal_id=? ORDER BY created_at DESC LIMIT 1", (did,)).fetchone()
        result = json.loads(row["result_json"])
        result.pop("evidence_digest")
        connection.execute("UPDATE underwritings SET result_json=? WHERE id=?", (json.dumps(result), row["id"]))
    assert item(app, did)["decision"] == "research"
    assert item(app, did)["current_criteria_fit_buyers"] == 0


@pytest.mark.parametrize("days", [-40, 1])
def test_stale_and_future_fact_dates_cannot_pass_review(tmp_path, days):
    app, did, pid = setup_deal(tmp_path)
    with app.database.session(write=True) as (_, memory):
        for record in memory.facts.values():
            record.observed_at = utc_now() + timedelta(days=days)
    app.underwrite(did, app.state()["deals"][0]["underwriting"]["inputs"])
    plan(app, did, seller_price=80000)
    assert any("stale or future-dated" in r for r in item(app, did)["reasons"])


def test_fixed_price_downside_and_portfolio_limits(tmp_path):
    app, did, _ = setup_deal(tmp_path)
    plan(app, did)  # Base +$12,000; downside -$8,000, at unchanged seller price.
    result = item(app, did)
    assert result["decision"] == "blocked"
    assert result["economics"]["downside"]["net_contribution"] == -8000
    plan(app, did, seller_price=80000)
    policy(app, max_deal_cash_at_risk=5000, max_portfolio_cash_at_risk=5000)
    assert any("portfolio cash exposure exceeds" in r for r in item(app, did)["reasons"])
    assert any("deal cash exposure exceeds" in r for r in item(app, did)["reasons"])


def test_unknown_portfolio_exposure_and_lost_cash_still_count(tmp_path):
    app, did, pid = setup_deal(tmp_path)
    other = app.create_deal({"property_id": pid, "strategy": "assignment"})
    assert app.state()["opportunities"]["unknown_exposure_deal_ids"] == [other["id"]]
    assert item(app, did)["decision"] == "research"
    cash(app, other["id"], "expense", 500)
    app.advance_deal(other["id"], {"stage": "lost", "note": "Synthetic loss"})
    queue = app.state()["opportunities"]
    assert queue["estimated_portfolio_cash_at_risk"] == 6500
    assert not queue["unknown_exposure_deal_ids"]
    assert all(i["deal_id"] != other["id"] for i in queue["items"])


def test_buy_box_exclusions_are_explained_and_policy_versions_retained(tmp_path):
    app, did, _ = setup_deal(tmp_path)
    first = app.state()["opportunities"]["policy"]["id"]
    policy(app, markets=["Indianapolis, IN"], max_seller_price=70000)
    result = item(app, did)
    assert result["decision"] == "outside_buy_box"
    assert any("Market is outside" in r for r in result["reasons"])
    assert any("Seller price exceeds" in r for r in result["reasons"])
    with app.database.session() as (connection, _):
        assert connection.execute("SELECT count(*) FROM opportunity_policies").fetchone()[0] == 2
        assert connection.execute("SELECT id FROM opportunity_policies WHERE id=?", (first,)).fetchone()


@pytest.mark.parametrize("changes", [{"markets": []}, {"strategies": ["unknown"]},
    {"evidence_max_age_days": True}, {"evidence_max_age_days": 0},
    {"max_seller_price": 0}, {"min_downside_net": -1},
    {"max_deal_cash_at_risk": 30000}, {"max_seller_price": "nan"}])
def test_invalid_policy_rolls_back(tmp_path, changes):
    app, _ = fixture_deal(tmp_path)
    with pytest.raises(ValueError):
        policy(app, **changes)
    assert app.state()["opportunities"]["policy"] is None


def test_http_policy_and_queue(http_app):
    code, body, _ = request(http_app, "/api/opportunities/policy", {
        "markets": ["Fort Wayne, IN"], "strategies": ["assignment"], "property_types": ["single_family"],
        "max_seller_price": 100000, "max_deal_cash_at_risk": 1000,
        "max_portfolio_cash_at_risk": 2000, "min_downside_net": 0,
        "evidence_max_age_days": 30, "basis": "Synthetic test policy"})
    assert code == 201
    saved = json.loads(body)
    code, body, _ = request(http_app, "/api/state")
    assert code == 200
    assert json.loads(body)["opportunities"]["policy"]["id"] == saved["id"]
