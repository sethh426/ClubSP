from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from uuid import uuid4

import pytest

from app.money import cents
from app.operations import business_today
from app.service import Application


def fixture_deal(tmp_path, strategy="assignment"):
    app = Application(tmp_path / "money.db")
    prop = app.create_property({"address": "Synthetic 123", "city": "Fort Wayne", "state": "IN"})
    deal = app.create_deal({"property_id": prop["id"], "strategy": strategy})
    app.underwrite(deal["id"], {
        "property_type": "single_family", "expected_exit_price": 240000,
        "buyer_repairs": 30000, "buyer_funding_holding": 8000,
        "buyer_closing": 5000, "buyer_selling_costs": 10000,
        "buyer_minimum_profit": 42000, "target_assignment_fee": 20000,
        "owner_transaction_costs": 4000, "partner_payout_allowance": 2000,
        "contingency": 2000, "desired_owner_net": 10000, "basis": "Synthetic assumptions",
    })
    return app, deal["id"]


def plan(app, deal_id, **changes):
    data = {"seller_price": 125000, "assignment_fee": 20000,
            "planned_cash_at_risk": 6000, "max_cash_at_risk": 10000, "basis": "Synthetic terms and owner funds"}
    data.update(changes)
    return app.save_financial_plan(deal_id, data)


def cash(app, deal_id, kind, amount, category=None, **changes):
    category = category or {"expense": "transaction", "income": "assignment_fee", "escrow_deposit": "earnest_money", "escrow_return": "earnest_money", "escrow_applied": "purchase", "escrow_forfeit": "earnest_money_loss"}[kind]
    data = {"entry_key": str(uuid4()), "kind": kind, "category": category, "amount": amount,
            "occurred_on": business_today().isoformat(), "evidence_reference": "synthetic-bank-record", "note": "Test transaction"}
    data.update(changes)
    return app.record_ledger_entry(deal_id, data)


def state_deal(app, deal_id):
    return next(d for d in app.state()["deals"] if d["id"] == deal_id)


def clear_tasks(app, deal_id, stage):
    for task in state_deal(app, deal_id)["tasks"]:
        if task["blocking_stage"] in {stage, "any"} and task["status"] == "open":
            app.resolve_task(task["id"], {"status": "done", "note": "Owner reviewed synthetic evidence", "evidence_reference": "synthetic-review"})


def finish_deal(app, deal_id):
    app.advance_deal(deal_id, {"stage": "offer_decision", "note": "Reviewed"})
    clear_tasks(app, deal_id, "contracted")
    app.advance_deal(deal_id, {"stage": "contracted", "note": "Reviewed", "owner_confirmed_signed": True, "evidence_reference": "synthetic-signed-contract"})
    clear_tasks(app, deal_id, "closing")
    app.advance_deal(deal_id, {"stage": "closing", "note": "Conditions reviewed"})
    clear_tasks(app, deal_id, "completed")
    app.advance_deal(deal_id, {"stage": "completed", "note": "Closing evidence reviewed", "owner_confirmed_closed": True, "evidence_reference": "synthetic-closing"})


def reconciliation(app, deal_id):
    return app.reconcile_deal(deal_id, {"owner_confirmed_complete": True, "note": "All synthetic costs and funds recorded", "evidence_reference": "synthetic-complete-ledger"})


def test_exact_money_and_invalid_amounts():
    assert cents("10.10") + cents("0.20") == 1030
    for value in (True, None, "nan", "Infinity", -1, "1.001", "1e50"):
        with pytest.raises(ValueError):
            cents(value)


def test_fixed_price_downside_and_risk_price_gates(tmp_path):
    app, did = fixture_deal(tmp_path)
    finance = plan(app, did)
    assert finance["plan"]["forecasts"]["base"]["net_contribution"] == 12000
    assert finance["plan"]["forecasts"]["downside"]["seller_price"] == 125000
    assert finance["plan"]["forecasts"]["downside"]["supports_entered_terms"] is False
    assert finance["plan"]["forecasts"]["downside"]["net_contribution"] == -8000
    assert not finance["contract_blockers"]
    over_price = plan(app, did, seller_price=126000)
    assert "Seller price exceeds the base underwriting ceiling" in over_price["contract_blockers"]
    over_risk = plan(app, did, planned_cash_at_risk=11000)
    assert "Owner cash exposure exceeds the chosen limit" in over_risk["contract_blockers"]
    app.advance_deal(did, {"stage": "offer_decision", "note": "Reviewed"})
    clear_tasks(app, did, "contracted")
    with pytest.raises(ValueError, match="cash exposure"):
        app.advance_deal(did, {"stage": "contracted", "note": "Blocked", "owner_confirmed_signed": True, "evidence_reference": "fixture"})


def test_escrow_is_not_double_counted_as_a_cost(tmp_path):
    app, did = fixture_deal(tmp_path, "resale")
    plan(app, did, seller_price=169000, assignment_fee=0, planned_cash_at_risk=230000, max_cash_at_risk=250000)
    cash(app, did, "escrow_deposit", 3000)
    interim = state_deal(app, did)["finance"]["summary"]
    assert interim["net_contribution"] == 0
    assert interim["unrecovered_cash"] == 3000
    cash(app, did, "escrow_applied", 3000)
    cash(app, did, "expense", 166000, "purchase")
    cash(app, did, "expense", 61000, "other")
    cash(app, did, "income", 240000, "resale_proceeds")
    summary = state_deal(app, did)["finance"]["summary"]
    assert summary["escrow_held"] == 0
    assert summary["net_contribution"] == 10000
    assert summary["cash_net"] == 10000
    assert summary["costs_by_category"]["purchase"] == 169000
    finish_deal(app, did)
    result = reconciliation(app, did)
    assert result["actual_net"] == result["forecast_net"] == 10000
    assert result["variance"] == 0
    assert Application(tmp_path / "money.db").state()["scorecard"]["reconciled_net_contribution"] == 10000


def test_lost_deal_tracks_forfeit_and_reconciliation_revisions(tmp_path):
    app, did = fixture_deal(tmp_path)
    cash(app, did, "escrow_deposit", 1000)
    original = cash(app, did, "expense", "300.10")
    app.advance_deal(did, {"stage": "lost", "note": "Seller withdrew"})
    with pytest.raises(ValueError, match="Resolve escrow"):
        reconciliation(app, did)
    cash(app, did, "escrow_forfeit", 1000)
    first = reconciliation(app, did)
    assert first["actual_net"] == -1300.10
    assert reconciliation(app, did)["id"] == first["id"]
    cash(app, did, "expense", "300.10", reversal_of=original["id"], note="Duplicate expense corrected")
    assert state_deal(app, did)["finance"]["reconciliation"]["current"] is False
    assert app.state()["scorecard"]["reconciled_net_contribution"] == 0
    second = reconciliation(app, did)
    assert second["actual_net"] == -1000
    assert app.state()["scorecard"]["reconciled_net_contribution"] == -1000


def test_duplicate_cash_retries_and_concurrency_are_safe(tmp_path):
    app, did = fixture_deal(tmp_path)
    key = str(uuid4())
    def write():
        return cash(app, did, "expense", "10.10", entry_key=key)["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: write(), range(2)))
    assert ids[0] == ids[1]
    assert len(state_deal(app, did)["finance"]["entries"]) == 1
    with pytest.raises(ValueError, match="different ledger data"):
        cash(app, did, "expense", 20, entry_key=key)


def test_bad_escrow_or_reversal_rolls_back(tmp_path):
    app, did = fixture_deal(tmp_path)
    with pytest.raises(ValueError, match="exceeds the deposit"):
        cash(app, did, "escrow_return", 1)
    dep = cash(app, did, "escrow_deposit", 1000)
    cash(app, did, "escrow_return", 1000)
    before = json.dumps(app.state(), default=str, sort_keys=True)
    with pytest.raises(ValueError, match="exceeds the deposit"):
        cash(app, did, "escrow_deposit", 1000, reversal_of=dep["id"])
    assert json.dumps(app.state(), default=str, sort_keys=True) == before


def test_completed_closing_is_distinct_from_received_funds(tmp_path):
    app, did = fixture_deal(tmp_path)
    plan(app, did)
    finish_deal(app, did)
    with pytest.raises(ValueError, match="money actually received"):
        reconciliation(app, did)
    cash(app, did, "income", 20000)
    cash(app, did, "expense", 6000)
    rec = reconciliation(app, did)
    assert rec["actual_net"] == 14000
    assert rec["forecast_net"] == 12000
    assert rec["variance"] == 2000


def test_operations_are_seeded_once_and_exception_deadline_blocks_stage(tmp_path):
    app, did = fixture_deal(tmp_path)
    assert len(state_deal(app, did)["tasks"]) == 8
    again = Application(tmp_path / "money.db")
    assert len(state_deal(again, did)["tasks"]) == 8
    task = app.create_task(did, {"operation": 13, "title": "Synthetic title issue", "owner": "Owner", "expected_result": "Professional review reference", "kind": "exception", "blocking_stage": "any", "due_on": (business_today()-timedelta(days=1)).isoformat()})
    assert app.state()["scorecard"]["open_exceptions"] == 1
    assert app.state()["scorecard"]["overdue_tasks"] == 1
    with pytest.raises(ValueError, match="Synthetic title issue"):
        app.advance_deal(did, {"stage": "offer_decision", "note": "Should be blocked"})
    with pytest.raises(ValueError, match="evidence_reference"):
        app.resolve_task(task["id"], {"status": "done", "note": "Reviewed"})
    app.schedule_task(task["id"], {"owner": "Seth", "due_on": business_today().isoformat(), "note": "Review today"})
    app.resolve_task(task["id"], {"status": "done", "note": "Resolved with evidence", "evidence_reference": "synthetic-review"})
    assert app.state()["scorecard"]["overdue_tasks"] == 0
    app.advance_deal(did, {"stage": "offer_decision", "note": "Can proceed"})


def test_plan_is_stale_after_underwriting_changes(tmp_path):
    app, did = fixture_deal(tmp_path)
    plan(app, did)
    inputs = state_deal(app, did)["underwriting"]["inputs"]
    inputs["buyer_repairs"] = 40000
    app.underwrite(did, inputs)
    assert "Financial plan must reference the latest underwriting" in state_deal(app, did)["finance"]["contract_blockers"]
    assert next(t for t in state_deal(app, did)["tasks"] if t["system_key"] == "money_plan")["status"] == "open"
