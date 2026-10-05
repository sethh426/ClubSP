from uuid import uuid4

import pytest

from app.operations import business_today
from app.service import Application


def setup_deal(tmp_path):
    app = Application(tmp_path / "transactions.db")
    prop = app.create_property({"address": "100 Synthetic Ave", "city": "Fort Wayne", "state": "IN"})
    deal = app.create_deal({"property_id": prop["id"], "strategy": "assignment"})
    app.underwrite(deal["id"], {
        "property_type": "single_family", "expected_exit_price": 240000,
        "buyer_repairs": 30000, "buyer_funding_holding": 8000,
        "buyer_closing": 5000, "buyer_selling_costs": 10000,
        "buyer_minimum_profit": 42000, "target_assignment_fee": 20000,
        "owner_transaction_costs": 4000, "partner_payout_allowance": 2000,
        "contingency": 2000, "desired_owner_net": 10000,
        "basis": "Synthetic transaction fixture",
    })
    app.save_financial_plan(deal["id"], {
        "seller_price": 125000, "assignment_fee": 20000,
        "planned_cash_at_risk": 6000, "max_cash_at_risk": 10000,
        "basis": "Synthetic plan",
    })
    app.advance_deal(deal["id"], {"stage": "offer_decision", "note": "Owner reviewed"})
    state = next(d for d in app.state()["deals"] if d["id"] == deal["id"])
    return app, deal["id"], state["underwriting"]["id"], state["finance"]["plan"]["id"]


def clear_contract_tasks(app, deal_id):
    deal = next(d for d in app.state()["deals"] if d["id"] == deal_id)
    for task in deal["tasks"]:
        if task["blocking_stage"] in {"contracted", "any"} and task["status"] == "open":
            app.resolve_task(task["id"], {
                "status": "done", "note": "Synthetic review complete",
                "evidence_reference": "synthetic-contract-gate",
            })


def executed_document(app, deal_id, underwriting_id, plan_id, **overrides):
    data = {
        "request_key": str(uuid4()),
        "previous_id": "",
        "underwriting_id": underwriting_id,
        "financial_plan_id": plan_id,
        "document_kind": "purchase_agreement",
        "document_reference": "private://agreement.pdf",
        "version_reference": "sha256:synthetic-v1",
        "status": "executed",
        "signature_status": "fully_signed",
        "title_status": "conditions_pending",
        "professional_review_reference": "closing-attorney-review-1",
        "title_review_reference": "",
        "closing_professional": "Synthetic Title Co",
        "effective_on": business_today().isoformat(),
        "expires_on": "",
        "note": "Synthetic executed agreement",
        "owner_confirmed_review": True,
    }
    data.update(overrides)
    return app.save_transaction_document(deal_id, data)


def test_transaction_document_binds_to_current_economics(tmp_path):
    app, did, uw, plan = setup_deal(tmp_path)
    document = executed_document(app, did, uw, plan)
    assert document["status"] == "executed"
    assert document["signature_status"] == "fully_signed"
    state = app.state()["transactions"][did]
    assert state["fully_signed_current_document"] is True
    assert state["funds_are_separate_from_signatures"] is True

    inputs = next(d for d in app.state()["deals"] if d["id"] == did)["underwriting"]["inputs"]
    inputs["buyer_repairs"] = 35000
    app.underwrite(did, inputs)
    with pytest.raises(ValueError, match="Current underwriting and financial plan"):
        executed_document(app, did, uw, plan)


def test_executed_legal_document_requires_full_signature_and_professional_review(tmp_path):
    app, did, uw, plan = setup_deal(tmp_path)
    with pytest.raises(ValueError, match="fully signed"):
        executed_document(app, did, uw, plan, signature_status="partially_signed")
    with pytest.raises(ValueError, match="professional review"):
        executed_document(app, did, uw, plan, professional_review_reference="")


def test_title_clearance_requires_professional_reference(tmp_path):
    app, did, uw, plan = setup_deal(tmp_path)
    with pytest.raises(ValueError, match="title review"):
        executed_document(
            app, did, uw, plan,
            title_status="cleared_by_professional",
            title_review_reference="",
        )


def test_document_versions_cannot_fork(tmp_path):
    app, did, uw, plan = setup_deal(tmp_path)
    first = app.save_transaction_document(did, {
        "request_key": str(uuid4()), "previous_id": "",
        "underwriting_id": uw, "financial_plan_id": plan,
        "document_kind": "purchase_agreement",
        "document_reference": "private://draft-v1.pdf",
        "version_reference": "v1", "status": "draft",
        "signature_status": "not_signed", "title_status": "unknown",
        "professional_review_reference": "", "title_review_reference": "",
        "closing_professional": "", "effective_on": "", "expires_on": "",
        "note": "First draft", "owner_confirmed_review": False,
    })
    second_payload = {
        "request_key": str(uuid4()), "previous_id": first["id"],
        "underwriting_id": uw, "financial_plan_id": plan,
        "document_kind": "purchase_agreement",
        "document_reference": "private://draft-v2.pdf",
        "version_reference": "v2", "status": "draft",
        "signature_status": "not_signed", "title_status": "unknown",
        "professional_review_reference": "", "title_review_reference": "",
        "closing_professional": "", "effective_on": "", "expires_on": "",
        "note": "Second draft", "owner_confirmed_review": False,
    }
    second = app.save_transaction_document(did, second_payload)
    assert second["previous_id"] == first["id"]
    fork = dict(second_payload, request_key=str(uuid4()), previous_id=first["id"], version_reference="fork")
    with pytest.raises(ValueError, match="supersede"):
        app.save_transaction_document(did, fork)


def test_conditions_and_closing_states_are_evidence_gated(tmp_path):
    app, did, uw, plan = setup_deal(tmp_path)
    executed_document(app, did, uw, plan)
    clear_contract_tasks(app, did)
    app.advance_deal(did, {
        "stage": "contracted", "note": "Synthetic contract",
        "owner_confirmed_signed": True, "evidence_reference": "private://agreement.pdf",
    })
    condition = app.save_transaction_condition(did, {
        "request_key": str(uuid4()), "previous_id": "",
        "name": "Resolve title exception", "category": "title", "status": "open",
        "owner": "Closing professional", "due_on": business_today().isoformat(),
        "evidence_reference": "", "professional_reference": "",
        "note": "Synthetic title exception",
    })
    app.record_closing_event(did, {
        "request_key": str(uuid4()), "state": "conditions_pending",
        "evidence_reference": "closing-file-1", "professional_reference": "",
        "note": "Conditions open", "occurred_on": business_today().isoformat(),
    })
    with pytest.raises(ValueError, match="Open transaction conditions"):
        app.record_closing_event(did, {
            "request_key": str(uuid4()), "state": "ready_by_professional",
            "evidence_reference": "ready-1", "professional_reference": "closer-1",
            "note": "Should block", "occurred_on": business_today().isoformat(),
        })
    resolved = app.save_transaction_condition(did, {
        "request_key": str(uuid4()), "previous_id": condition["id"],
        "name": "Resolve title exception", "category": "title", "status": "satisfied",
        "owner": "Closing professional", "due_on": business_today().isoformat(),
        "evidence_reference": "title-clearance-evidence",
        "professional_reference": "closer-1",
        "note": "Synthetic title condition satisfied",
    })
    assert resolved["status"] == "satisfied"
    ready = app.record_closing_event(did, {
        "request_key": str(uuid4()), "state": "ready_by_professional",
        "evidence_reference": "ready-2", "professional_reference": "closer-1",
        "note": "Professional says ready", "occurred_on": business_today().isoformat(),
    })
    signed = app.record_closing_event(did, {
        "request_key": str(uuid4()), "state": "signed",
        "evidence_reference": "signed-closing-package", "professional_reference": "",
        "note": "Signed only; funds not yet recorded", "occurred_on": business_today().isoformat(),
    })
    assert ready["state"] == "ready_by_professional"
    assert signed["state"] == "signed"
    assert app.state()["transactions"][did]["latest_closing_state"] == "signed"
    with pytest.raises(ValueError, match="cannot move"):
        app.record_closing_event(did, {
            "request_key": str(uuid4()), "state": "recorded_complete",
            "evidence_reference": "skip", "professional_reference": "closer-1",
            "note": "Cannot skip funded state", "occurred_on": business_today().isoformat(),
        })


def test_funded_and_complete_are_distinct_and_persist(tmp_path):
    path = tmp_path / "transactions.db"
    app, did, uw, plan = setup_deal(tmp_path)
    executed_document(app, did, uw, plan)
    clear_contract_tasks(app, did)
    app.advance_deal(did, {
        "stage": "contracted", "note": "Synthetic contract",
        "owner_confirmed_signed": True, "evidence_reference": "private://agreement.pdf",
    })
    for state, professional in [
        ("conditions_pending", ""),
        ("ready_by_professional", "closer-1"),
        ("signed", ""),
        ("funded_disbursed", "closer-1"),
        ("recorded_complete", "closer-1"),
    ]:
        app.record_closing_event(did, {
            "request_key": str(uuid4()), "state": state,
            "evidence_reference": "evidence-" + state,
            "professional_reference": professional,
            "note": "Synthetic " + state,
            "occurred_on": business_today().isoformat(),
        })
    state = app.state()["transactions"][did]
    assert state["latest_closing_state"] == "recorded_complete"
    closing_task = next(
        t for t in next(d for d in app.state()["deals"] if d["id"] == did)["tasks"]
        if t["system_key"] == "closing_evidence"
    )
    assert closing_task["status"] == "done"

    restarted = Application(path)
    assert restarted.state()["transactions"][did]["latest_closing_state"] == "recorded_complete"
