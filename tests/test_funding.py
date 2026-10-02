from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from app.funding import FundingBook
from app.operations import business_today
from app.server import create_server
from app.service import Application
from tests.test_app import underwriting_payload
from tests.test_finance_operations import fixture_deal, plan


def review(**changes):
    data = {"request_key": str(uuid4()), "kind": "lender", "counterparty": "Synthetic lender",
        "required_funding": "130000.10", "committed_funding": "130000.10",
        "owner_cash_required": "6000", "contingent_liability": "0", "known_financing_cost": "2000",
        "status": "owner_reviewed", "owner_confirmed": True,
        "reviewed_on": business_today().isoformat(),
        "expires_on": (business_today() + timedelta(days=30)).isoformat(),
        "unresolved_conditions": [], "note": "Synthetic records; no real funds promised",
        "funding_need_reference": "synthetic-need", "terms_reference": "synthetic-terms",
        "allocation_reference": "synthetic-allocation", "obligations_reference": "synthetic-obligations",
        "costs_reference": "synthetic-costs", "conditions_reference": "synthetic-conditions"}
    data.update(changes)
    return data


def setup(tmp_path, strategy="resale"):
    app, did = fixture_deal(tmp_path, strategy)
    plan(app, did, assignment_fee=0 if strategy == "resale" else 20000)
    return app, did, FundingBook(app.database)


def with_context(book, did, data):
    with book.database.session() as (connection, _):
        plan = connection.execute("SELECT id,underwriting_id FROM financial_plans WHERE deal_id=? ORDER BY created_at DESC,id LIMIT 1", (did,)).fetchone()
    return {"financial_plan_id": plan["id"], "underwriting_id": plan["underwriting_id"], **data}


def submit(book, did, data):
    return book.save(did, with_context(book, did, data))


def funded_deal(app, book, did):
    return next(d for d in book.state(app.state())["deals"] if d["id"] == did)


def test_unknown_funding_does_not_become_zero_or_pass(tmp_path):
    app, did, book = setup(tmp_path)
    result = funded_deal(app, book, did)
    assert result["funding_gap"] is None
    assert not result["checks_pass"]
    assert result["review"] is None


def test_review_survives_restart_and_remains_advisory(tmp_path):
    app, did, book = setup(tmp_path)
    saved = submit(book, did, review())
    assert saved["committed_funding_cents"] == 13000010
    restarted = Application(app.database.path)
    result = FundingBook(restarted.database).state(restarted.state())
    assert result["mode"] == "owner_entered_advisory"
    assert result["summary"]["checks_pass"] == 1
    row = result["deals"][0]
    assert row["history"][0]["id"] == saved["id"]
    assert row["funding_gap"] == 0
    assert "Confirm availability" in row["next_action"]


@pytest.mark.parametrize("changes,reason", [
    ({"committed_funding": "129000.10"}, "does not cover"),
    ({"unresolved_conditions": ["Appraisal approval"]}, "Appraisal approval"),
    ({"reviewed_on": (business_today()-timedelta(days=3)).isoformat(), "expires_on": (business_today()-timedelta(days=1)).isoformat()}, "expired"),
    ({"status": "pending", "owner_confirmed": False}, "pending owner review"),
    ({"status": "withdrawn"}, "withdrawn"),
    ({"owner_cash_required": "7000"}, "more owner cash"),
    ({"contingent_liability": "5000"}, "exposure exceeds"),
    ({"known_financing_cost": "20000"}, "cost allowances"),
    ({"required_funding": "100000", "committed_funding": "100000"}, "even the purchase price"),
])
def test_missing_or_adverse_conditions_block_recorded_checks(tmp_path, changes, reason):
    app, did, book = setup(tmp_path)
    submit(book, did, review(**changes))
    result = funded_deal(app, book, did)
    assert not result["checks_pass"]
    assert any(reason in b for b in result["blockers"])


def test_changed_terms_require_a_new_review_without_erasing_history(tmp_path):
    app, did, book = setup(tmp_path)
    first = submit(book, did, review())
    plan(app, did, assignment_fee=0, seller_price=124000)
    row = funded_deal(app, book, did)
    assert not row["review_matches_current_terms"]
    assert not row["checks_pass"]
    second = submit(book, did, review(supersedes_id=first["id"], note="Reviewed revised synthetic terms"))
    row = funded_deal(app, book, did)
    assert row["checks_pass"]
    assert [r["id"] for r in row["history"]] == [second["id"], first["id"]]


def test_changed_underwriting_blocks_stale_plan_and_funding(tmp_path):
    app, did, book = setup(tmp_path)
    old = submit(book, did, review())
    app.underwrite(did, underwriting_payload(expected_exit_price=250000))
    with pytest.raises(ValueError, match="current underwriting"):
        submit(book, did, review(supersedes_id=old["id"]))
    assert not funded_deal(app, book, did)["checks_pass"]


def test_idempotency_and_concurrent_revision_protect_history(tmp_path):
    app, did, book = setup(tmp_path)
    payload = review()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: submit(book, did, payload)["id"], range(2)))
    assert ids[0] == ids[1]
    with pytest.raises(ValueError, match="different funding data"):
        submit(book, did, {**payload, "committed_funding": 99999})
    with pytest.raises(ValueError, match="latest funding review"):
        submit(book, did, review())
    submit(book, did, review(supersedes_id=ids[0]))
    with pytest.raises(ValueError, match="latest funding review"):
        submit(book, did, review(supersedes_id=ids[0]))
    assert len(funded_deal(app, book, did)["history"]) == 2


@pytest.mark.parametrize("changes", [
    {"owner_confirmed": "true"}, {"allocation_reference": ""}, {"costs_reference": ""},
    {"conditions_reference": ""}, {"obligations_reference": ""}, {"terms_reference": ""},
    {"funding_need_reference": ""}, {"reviewed_on": "20261001"},
    {"reviewed_on": (business_today()+timedelta(days=1)).isoformat()},
    {"expires_on": (business_today()-timedelta(days=1)).isoformat()},
    {"committed_funding": "1.001"}, {"required_funding": 0}, {"required_funding": True},
    {"known_financing_cost": "NaN"}, {"contingent_liability": -1},
    {"unresolved_conditions": "unknown"}, {"status": "approved_by_bank"},
])
def test_invalid_reviews_do_not_leave_partial_records(tmp_path, changes):
    app, did, book = setup(tmp_path)
    with pytest.raises(ValueError):
        submit(book, did, review(**changes))
    assert not funded_deal(app, book, did)["history"]


def test_reused_allocation_reference_blocks_both_active_deals(tmp_path):
    app, did, book = setup(tmp_path)
    prop = app.create_property({"address": "Second synthetic property", "city": "Fort Wayne", "state": "IN"})
    second = app.create_deal({"property_id": prop["id"], "strategy": "resale"})["id"]
    app.underwrite(second, underwriting_payload())
    plan(app, second, assignment_fee=0)
    submit(book, did, review())
    submit(book, second, review())
    rows = book.state(app.state())["deals"]
    assert all(not d["checks_pass"] for d in rows)
    assert all(any("reused" in b for b in d["blockers"]) for d in rows)


def test_ended_deals_keep_history_without_new_funding_activity(tmp_path):
    app, did, book = setup(tmp_path)
    first = submit(book, did, review())
    app.advance_deal(did, {"stage": "lost", "note": "Synthetic loss"})
    with pytest.raises(ValueError, match="Ended deals"):
        submit(book, did, review(supersedes_id=first["id"]))
    row = funded_deal(app, book, did)
    assert row["ended"] and not row["can_record"] and not row["checks_pass"]
    assert len(row["history"]) == 1
    assert book.state(app.state())["summary"]["active_deals"] == 0


def test_http_funding_workflow_and_origin_guard(tmp_path):
    app, did, book = setup(tmp_path)
    server = create_server(app.database.path, port=0, application=app)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = "http://127.0.0.1:" + str(server.server_address[1])
    try:
        req = Request(origin + "/api/deals/" + did + "/funding", data=json.dumps(with_context(book, did, review())).encode(),
            headers={"Content-Type": "application/json", "Origin": origin}, method="POST")
        with urlopen(req) as response:
            assert response.status == 201
        with urlopen(origin + "/api/funding") as response:
            assert json.load(response)["summary"]["checks_pass"] == 1
        with urlopen(origin + "/funding") as response:
            assert b"A recorded path to funding" in response.read()
            assert "script-src 'self'" in response.headers["Content-Security-Policy"]
        bad = Request(origin + "/api/deals/" + did + "/funding", data=json.dumps(with_context(book, did, review())).encode(),
            headers={"Content-Type": "application/json", "Origin": "https://untrusted.example"}, method="POST")
        with pytest.raises(HTTPError) as failure:
            urlopen(bad)
        assert failure.value.code == 403
        assert len(funded_deal(app, book, did)["history"]) == 1
    finally:
        server.shutdown(); server.server_close(); worker.join()


def test_terms_changed_while_form_was_open_cannot_bind_old_review_to_new_plan(tmp_path):
    app, did, book = setup(tmp_path)
    old_payload = with_context(book, did, review())
    plan(app, did, assignment_fee=0, seller_price=124000)
    with pytest.raises(ValueError, match="changed during review"):
        book.save(did, old_payload)
    assert not funded_deal(app, book, did)["history"]
