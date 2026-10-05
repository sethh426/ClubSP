from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from app.operations import business_today
from app.relationships import RelationshipBook
from app.server import create_server
from app.service import Application
from tests.test_communications import setup_contact, permit, message


def profile(**changes):
    return {"request_key": str(uuid4()), "name": "Synthetic Investor", "company": "Synthetic only",
        "email": "investor@example.test", "kind": "investor", "status": "prospect", "needs": "Learn preferred areas",
        "source_reference": "synthetic-introduction", "permission": "unknown", "permission_reference": "",
        "owner": "Owner", "next_action": "Ask about current buying criteria", "markets": ["Fort Wayne, IN"],
        "follow_up_on": business_today().isoformat(), "buyer_id": "", **changes}


def interaction(record, **changes):
    return {"request_key": str(uuid4()), "profile_id": record["profile"]["id"], "event_id": record["event_id"] or "",
        "direction": "incoming", "outcome": "general", "note": "Synthetic conversation only",
        "evidence_reference": "synthetic-conversation", "occurred_on": business_today().isoformat(),
        "follow_up_on": "", "next_action": "Review the reply", **changes}


def setup(tmp_path):
    app = Application(tmp_path / "relationships.db")
    return app, RelationshipBook(app)


def current(book, rid=None):
    return next(r for r in book.state()["relationships"] if rid is None or r["id"] == rid)


def revise(book, row, **changes):
    fields = {k:v for k,v in row["profile"].items() if k in profile() and k != "request_key"}
    fields["buyer_id"] = fields.get("buyer_id") or ""
    fields.update(relationship_id=row["id"], profile_id=row["profile"]["id"], event_id=row["event_id"] or "")
    fields.update(changes)
    return book.save(profile(**fields))


def test_relationship_needs_no_property_or_qualified_buyer_and_survives_restart(tmp_path):
    app, book = setup(tmp_path)
    book.save(profile())
    assert not app.state()["properties"] and not app.state()["buyers"]
    state = RelationshipBook(Application(app.database.path)).state()
    assert state["summary"]["total"] == state["summary"]["due"] == 1
    assert state["sending_enabled"] is False
    assert current(book)["draft"] is None
    assert current(book)["buyer"] is None


def test_profile_idempotency_concurrent_retry_and_changed_payload(tmp_path):
    app, book = setup(tmp_path)
    data = profile()
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(lambda _: book.save(data)["id"], range(3)))
    assert len(set(ids)) == 1
    with pytest.raises(ValueError, match="different profile"):
        book.save({**data, "needs": "Different"})
    with pytest.raises(ValueError, match="already has a relationship"):
        book.save(profile(email="INVESTOR@example.test"))
    assert len(current(book)["profile_history"]) == 1


def test_optimistic_profile_and_interaction_context_rejects_stale_forms(tmp_path):
    _, book = setup(tmp_path)
    book.save(profile())
    row = current(book)
    old = interaction(row)
    first = book.interact(row["id"], old)
    assert book.interact(row["id"], old)["id"] == first["id"]
    with pytest.raises(ValueError, match="different interaction"):
        book.interact(row["id"], {**old, "note": "Changed"})
    with pytest.raises(ValueError, match="changed"):
        book.interact(row["id"], interaction(row))
    with pytest.raises(ValueError, match="changed"):
        revise(book, row)
    latest = current(book)
    new_profile = revise(book, latest, request_key=str(uuid4()), follow_up_on=business_today().isoformat())
    with pytest.raises(ValueError, match="changed"):
        book.interact(row["id"], interaction(latest))
    assert new_profile["previous_id"] == row["profile"]["id"]


def test_reply_clears_old_schedule_and_owner_can_record_new_next_step(tmp_path):
    _, book = setup(tmp_path)
    book.save(profile(permission="owner_reviewed", permission_reference="synthetic-permission"))
    row = current(book)
    assert row["due"] and row["draft"]["sending_enabled"] is False
    book.interact(row["id"], interaction(row))
    assert not current(book)["due"]
    assert current(book)["queue_status"] == "unscheduled"
    revise(book, current(book), request_key=str(uuid4()), follow_up_on=business_today().isoformat(), next_action="Respond to current priorities")
    assert current(book)["due"] and current(book)["next_action"] == "Respond to current priorities"


@pytest.mark.parametrize("changes", [
    {"name": ""}, {"kind": "seller"}, {"status": "signed_contract"}, {"permission": "approved"},
    {"source_reference": ""}, {"email": "bad\nheader@example.test"}, {"markets": "Fort Wayne"},
    {"markets": ["x" * 101]}, {"markets": [str(i) for i in range(21)]},
    {"permission": "owner_reviewed", "permission_reference": ""},
    {"follow_up_on": "20261003"}, {"follow_up_on": "2026-02-30"}, {"next_action": ""},
    {"buyer_id": "wrong"}, {"request_key": "wrong"}, {"profile_id": str(uuid4())},
])
def test_invalid_profile_is_atomic(tmp_path, changes):
    _, book = setup(tmp_path)
    with pytest.raises((ValueError, LookupError)):
        book.save(profile(**changes))
    assert book.state()["summary"]["total"] == 0


def test_missing_buyer_is_rejected_and_link_does_not_duplicate_buyer_criteria(tmp_path):
    app, book = setup(tmp_path)
    with pytest.raises(LookupError, match="buyer"):
        book.save(profile(buyer_id=str(uuid4())))
    buyer = app.create_buyer({"name": "Synthetic buyer", "locations": ["Fort Wayne, IN"],
        "strategies": ["assignment"], "property_types": [], "max_total_price": 150000,
        "max_repairs": 30000, "funding_status": "unverified", "company": "Synthetic only"})
    book.save(profile(buyer_id=buyer["id"]))
    row = current(book)
    assert row["buyer"]["id"] == buyer["id"]
    assert len(app.state()["buyers"]) == 1
    assert "max_total_price" not in row["profile"]


def test_stops_suppress_relationship_and_existing_and_future_property_contacts(tmp_path):
    app, _, contact = setup_contact(tmp_path)
    book = RelationshipBook(app)
    permit(app, contact)
    app.record_message(contact["id"], message(app, contact))
    draft = app.suggest_reply(contact["id"], {})
    book.save(profile(email=contact["email"], permission="owner_reviewed", permission_reference="synthetic-permission"))
    row = current(book)
    book.interact(row["id"], interaction(row, note="Please stop emailing me."))
    state = book.state()
    assert current(book)["blocked"] and current(book)["draft"] is None
    assert state["daily_focus"] == []
    seller = app.state()["communications"]["contacts"][0]
    assert seller["permission_status"] == "suppressed" and seller["drafts"][0]["status"] == "void"
    with pytest.raises(ValueError, match="suppression"):
        revise(book, current(book), request_key=str(uuid4()), email="new@example.test", permission="owner_reviewed")
    prop = app.create_property({"address": "Another synthetic property", "city": "Fort Wayne", "state": "IN"})
    future = app.create_contact({"property_id": prop["id"], "name": "Synthetic", "email": contact["email"], "role": "unverified"})
    assert future["permission_status"] == "suppressed"
    with pytest.raises(ValueError, match="suppression"):
        app.set_contact_permission(future["id"], {"status": "permitted", "note": "Synthetic", "evidence_reference": "synthetic"})


def test_property_suppression_is_respected_by_relationships(tmp_path):
    app, _, contact = setup_contact(tmp_path)
    book = RelationshipBook(app)
    book.save(profile(email=contact["email"], permission="owner_reviewed", permission_reference="synthetic"))
    app.record_message(contact["id"], message(app, contact, body="Unsubscribe"))
    assert current(book)["blocked"] and not current(book)["due"]
    assert not current(book)["draft"]


def test_stop_before_property_contact_exists_and_changed_email_cannot_bypass_stop(tmp_path):
    app, book = setup(tmp_path)
    book.save(profile())
    row = current(book)
    book.interact(row["id"], interaction(row, outcome="wrong_person"))
    revise(book, current(book), request_key=str(uuid4()), email="new@example.test")
    assert current(book)["blocked"]
    prop = app.create_property({"address": "Synthetic", "city": "Fort Wayne", "state": "IN"})
    contact = app.create_contact({"property_id": prop["id"], "name": "Synthetic", "email": "investor@example.test", "role": "unverified"})
    assert contact["permission_status"] == "suppressed"


def test_daily_focus_caps_ten_and_excludes_paused_and_blocked(tmp_path):
    _, book = setup(tmp_path)
    for i in range(12):
        book.save(profile(name=f"Synthetic {i:02}", email=f"synthetic-{i}@example.test", follow_up_on=(business_today()-timedelta(days=i)).isoformat()))
    book.save(profile(name="Paused synthetic", email="paused@example.test", status="paused"))
    book.save(profile(name="Blocked synthetic", email="blocked@example.test", permission="blocked"))
    state = book.state()
    assert state["summary"]["due"] == 12 and len(state["daily_focus"]) == 10
    assert state["relationships"][0]["profile"]["name"] == "Synthetic 11"
    assert all(not r["due"] for r in state["relationships"] if r["paused"] or r["blocked"])


@pytest.mark.parametrize("changes", [
    {"occurred_on": (business_today()+timedelta(days=1)).isoformat()},
    {"follow_up_on": (business_today()-timedelta(days=1)).isoformat(), "next_action": "Review"},
    {"direction": "send_now"}, {"outcome": "funding_verified"}, {"evidence_reference": ""},
    {"follow_up_on": business_today().isoformat(), "next_action": ""}, {"note": ""},
])
def test_invalid_interaction_does_not_remove_schedule(tmp_path, changes):
    _, book = setup(tmp_path)
    book.save(profile())
    row = current(book)
    with pytest.raises(ValueError):
        book.interact(row["id"], interaction(row, **changes))
    assert current(book)["due"] and not current(book)["interactions"]


def test_relationship_http_workflow_and_origin_guard(tmp_path):
    app, _ = setup(tmp_path)
    server = create_server(app.database.path, port=0, application=app)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    origin = "http://127.0.0.1:" + str(server.server_address[1])
    try:
        request = Request(origin + "/api/relationships", data=json.dumps(profile()).encode(),
            headers={"Content-Type": "application/json", "Origin": origin}, method="POST")
        with urlopen(request) as response: assert response.status == 201
        with urlopen(origin + "/api/relationships") as response: assert json.load(response)["summary"]["total"] == 1
        with urlopen(origin + "/relationships") as response:
            assert b"Relationships with a next step" in response.read()
            assert "script-src 'self'" in response.headers["Content-Security-Policy"]
        bad = Request(origin + "/api/relationships", data=json.dumps(profile(email="other@example.test")).encode(),
            headers={"Content-Type": "application/json", "Origin": "https://untrusted.example"}, method="POST")
        with pytest.raises(HTTPError) as error: urlopen(bad)
        assert error.value.code == 403
    finally:
        server.shutdown(); server.server_close(); worker.join()
