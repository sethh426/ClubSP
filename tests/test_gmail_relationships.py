from uuid import uuid4

import pytest

from app.operations import business_today
from app.relationships import RelationshipBook
from app.service import Application
from tests.test_gmail_inbox import message, preview
from tests.test_relationships import profile, current


def setup_reply(tmp_path, email="seller@example.test"):
    app = Application(tmp_path / "reply.db")
    book = RelationshipBook(app)
    book.save(profile(email=email, permission="owner_reviewed", permission_reference="synthetic-permission"))
    item = preview(message(), "owner@example.test", "abc123")
    app.save_gmail_previews([item])
    saved = app.gmail_inbox()["messages"][0]
    return app, book, saved


def test_preview_exposes_only_exact_email_relationship_candidates(tmp_path):
    app, book, saved = setup_reply(tmp_path)
    original = current(book)
    book.save(profile(email="other@example.test", name="Other Investor"))
    message_state = app.gmail_inbox()["messages"][0]
    assert [c["id"] for c in message_state["relationship_candidates"]] == [original["id"]]
    assert message_state["relationship_id"] is None


def test_link_requires_current_exact_sender_email_and_unlink_is_safe(tmp_path):
    app, book, saved = setup_reply(tmp_path)
    row = current(book)
    result = book.review_gmail_reply(saved["id"], {"action": "link", "relationship_id": row["id"]})
    assert result["relationship_id"] == row["id"]
    linked = app.gmail_inbox()["messages"][0]
    assert linked["relationship_id"] == row["id"] and linked["relationship_name"] == row["profile"]["name"]
    book.review_gmail_reply(saved["id"], {"action": "unlink"})
    assert app.gmail_inbox()["messages"][0]["relationship_id"] is None

    book.save(profile(email="other@example.test", name="Other Investor"))
    other = next(r for r in book.state()["relationships"] if r["profile"]["email"] == "other@example.test")
    with pytest.raises(ValueError, match="matches"):
        book.review_gmail_reply(saved["id"], {"action": "link", "relationship_id": other["id"]})


def test_reviewed_reply_import_is_idempotent_and_updates_followup_queue(tmp_path):
    app, book, saved = setup_reply(tmp_path)
    row = current(book)
    book.review_gmail_reply(saved["id"], {"action": "link", "relationship_id": row["id"]})
    key = str(uuid4())
    data = {
        "action": "import", "relationship_id": row["id"], "request_key": key,
        "outcome": "interested", "occurred_on": business_today().isoformat(),
        "review_note": "Buyer is still active and asked for current opportunities.",
        "follow_up_on": business_today().isoformat(), "next_action": "Send qualified opportunities only",
    }
    first = book.review_gmail_reply(saved["id"], data)
    again = book.review_gmail_reply(saved["id"], data)
    assert first["interaction"]["id"] == again["interaction"]["id"]
    record = current(book)
    assert record["interactions"][0]["direction"] == "incoming"
    assert record["interactions"][0]["outcome"] == "interested"
    assert "Gmail preview:" in record["interactions"][0]["note"]
    assert record["due"] and record["next_action"] == "Send qualified opportunities only"
    inbox = app.gmail_inbox()["messages"][0]
    assert inbox["relationship_interaction_id"] == first["interaction"]["id"]
    assert inbox["relationship_imported_at"]
    with pytest.raises(ValueError, match="already imported"):
        book.review_gmail_reply(saved["id"], {**data, "request_key": str(uuid4())})
    with pytest.raises(ValueError, match="cannot be unlinked"):
        book.review_gmail_reply(saved["id"], {"action": "unlink"})
    with pytest.raises(ValueError, match="retained"):
        app.review_gmail_preview(saved["id"], {"action": "remove"})


def test_stop_reply_immediately_suppresses_future_outreach(tmp_path):
    app, book, saved = setup_reply(tmp_path)
    row = current(book)
    book.review_gmail_reply(saved["id"], {"action": "link", "relationship_id": row["id"]})
    result = book.review_gmail_reply(saved["id"], {
        "action": "import", "relationship_id": row["id"], "request_key": str(uuid4()),
        "outcome": "stop", "occurred_on": business_today().isoformat(),
        "review_note": "Reviewed original Gmail message: explicit stop request.",
        "follow_up_on": business_today().isoformat(), "next_action": "Should be cleared",
    })
    assert result["interaction"]["outcome"] == "stop"
    record = current(book)
    assert record["blocked"] and not record["due"] and record["draft"] is None


def test_preview_removal_cleans_unimported_relationship_link(tmp_path):
    app, book, saved = setup_reply(tmp_path)
    row = current(book)
    book.review_gmail_reply(saved["id"], {"action": "link", "relationship_id": row["id"]})
    app.review_gmail_preview(saved["id"], {"action": "remove"})
    assert app.gmail_inbox()["total"] == 0
