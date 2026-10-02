from app.funding import FundingBook
from tests.test_funding import setup, submit, review


def test_funding_pass_does_not_hide_missing_opportunity_checks(tmp_path):
    app, did, book = setup(tmp_path)
    submit(book, did, review())
    result = book.state(app.state())
    row = result["deals"][0]
    assert row["checks_pass"]
    assert row["action_status"] == "needs_action"
    assert row["current_criteria_fit_buyers"] in {None, 0}
    assert not result["pipeline"]["execution_authorized"]
    assert row["pipeline_next_action"]
    assert row["downside_net"] is not None
    assert row["reconciled_net"] is None


def test_live_opportunity_reasons_and_tasks_remain_visible(tmp_path):
    app, did, book = setup(tmp_path)
    submit(book, did, review())
    state = app.state()
    state["opportunities"] = {"items": [{"deal_id": did, "decision": "blocked",
        "reasons": ["Comparable-sale evidence changed", "Buyer funding review expired"],
        "current_criteria_fit_buyers": 0}]}
    row = book.state(state)["deals"][0]
    assert row["checks_pass"]
    assert row["opportunity_decision"] == "blocked"
    assert row["pipeline_next_action"] == "Comparable-sale evidence changed"
    assert row["current_criteria_fit_buyers"] == 0
    assert any(action["category"] == "task" for action in row["action_items"])
    state["opportunities"]["items"][0].update(decision="owner_review", reasons=[], current_criteria_fit_buyers=1)
    state["deals"][0]["tasks"] = []
    result = book.state(state)
    assert result["deals"][0]["action_status"] == "owner_review"
    assert result["pipeline"]["owner_review_candidates"] == 1
    assert not result["pipeline"]["execution_authorized"]


def test_ended_deals_leave_shortlist_and_require_cash_reconciliation(tmp_path):
    app, did, book = setup(tmp_path)
    submit(book, did, review())
    app.advance_deal(did, {"stage": "lost", "note": "Synthetic loss"})
    state = app.state()
    result = book.state(state)
    row = result["deals"][0]
    assert row["action_status"] == "historical"
    assert result["pipeline"]["shortlist"] == []
    assert row["action_items"][0]["category"] == "reconciliation"
    state["deals"][0]["finance"]["reconciliation"] = {"current": True, "actual_net": -123.45}
    row = book.state(state)["deals"][0]
    assert row["action_items"] == []
    assert row["reconciled_net"] == -123.45


def test_no_plan_keeps_economics_unknown(tmp_path):
    app, did, book = setup(tmp_path)
    state = app.state()
    state["deals"][0]["finance"]["plan"] = None
    row = book.state(state)["deals"][0]
    assert row["forecast_net"] is None
    assert row["downside_net"] is None
    assert row["action_status"] == "needs_action"

