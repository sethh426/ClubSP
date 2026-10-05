import base64
from email import policy
from email.parser import BytesParser
from uuid import uuid4

import pytest

from app.gmail_send import SEND_URL, send_approved_draft
from tests.test_gmail import make_connection, finish
from tests.test_relationships import current, interaction
from tests.test_relationship_drafts import ready, review_data


def approved(tmp_path):
    app, book, row = ready(tmp_path)
    draft = row["saved_drafts"][0]
    book.review_draft(row["id"], review_data(draft))
    row = current(book)
    draft = row["saved_drafts"][0]
    gmail, _ = make_connection(tmp_path / "private")
    finish(gmail)
    return app, book, row, draft, gmail


def send_data(draft, **changes):
    return {
        "request_key": str(uuid4()),
        "draft_id": draft["id"],
        "review_id": draft["reviews"][0]["id"],
        "owner_confirmed_send": True,
        "follow_up_on": "",
        "next_action": "",
        **changes,
    }


def test_approved_draft_sends_once_and_persists_exact_receipt(tmp_path):
    _, book, row, draft, gmail = approved(tmp_path)
    calls = []
    def request(url, **kwargs):
        calls.append((url, kwargs))
        if url == SEND_URL:
            return {"id": "msg_123", "threadId": "thread_456"}
        return {"emailAddress": "owner@example.test"}
    gmail.request = request
    data = send_data(draft)
    result = send_approved_draft(gmail, book, row["id"], data)
    assert result["status"] == "sent"
    assert result["latest_event"]["provider_message_id"] == "msg_123"
    assert result["recipient"] == "investor@example.test"
    assert result["sender"] == "owner@example.test"
    assert result["subject"] == draft["subject"] and result["body"] == draft["body"]
    assert len(calls) == 1 and calls[0][0] == SEND_URL

    raw = calls[0][1]["json_body"]["raw"]
    raw += "=" * (-len(raw) % 4)
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
    assert message["From"] == "owner@example.test"
    assert message["To"] == "investor@example.test"
    assert message["Subject"] == draft["subject"]
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == draft["body"]
    assert "Bcc" not in message

    # Browser/network retry with the same idempotency key never sends twice.
    assert send_approved_draft(gmail, book, row["id"], data)["status"] == "sent"
    assert len(calls) == 1
    refreshed = current(book)
    saved = refreshed["saved_drafts"][0]
    assert saved["send"]["status"] == "sent" and not saved["sending_enabled"]
    with pytest.raises(ValueError, match="already has a send attempt"):
        send_approved_draft(gmail, book, row["id"], send_data(saved))


def test_provider_failure_becomes_unknown_and_is_never_auto_retried(tmp_path):
    _, book, row, draft, gmail = approved(tmp_path)
    calls = []
    def unavailable(url, **kwargs):
        calls.append(url)
        raise ValueError("synthetic timeout")
    gmail.request = unavailable
    data = send_data(draft)
    with pytest.raises(ValueError, match="unknown"):
        send_approved_draft(gmail, book, row["id"], data)
    saved = current(book)["saved_drafts"][0]["send"]
    assert saved["status"] == "unknown"
    assert "Verify the Sent mailbox" in saved["latest_event"]["note"]
    # Same request key returns the ledger state and cannot call Gmail again.
    assert send_approved_draft(gmail, book, row["id"], data)["status"] == "unknown"
    assert len(calls) == 1


def test_suppression_or_context_change_after_approval_blocks_transport(tmp_path):
    _, book, row, draft, gmail = approved(tmp_path)
    book.interact(row["id"], interaction(row, outcome="stop", note="Please stop contacting me."))
    gmail.request = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Gmail must not be called"))
    with pytest.raises(ValueError, match="Do not contact|changed"):
        send_approved_draft(gmail, book, row["id"], send_data(draft))
    assert current(book)["saved_drafts"][0]["send"] is None


def test_send_requires_current_approval_and_explicit_owner_confirmation(tmp_path):
    _, book, row = ready(tmp_path)
    draft = row["saved_drafts"][0]
    gmail, _ = make_connection(tmp_path / "private")
    finish(gmail)
    gmail.request = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Gmail must not be called"))
    with pytest.raises(ValueError, match="current review"):
        send_approved_draft(gmail, book, row["id"], {
            "request_key": str(uuid4()), "draft_id": draft["id"], "review_id": str(uuid4()),
            "owner_confirmed_send": True, "follow_up_on": "", "next_action": "",
        })
    book.review_draft(row["id"], review_data(draft))
    approved_draft = current(book)["saved_drafts"][0]
    with pytest.raises(ValueError, match="confirmation"):
        send_approved_draft(gmail, book, row["id"], send_data(approved_draft, owner_confirmed_send=False))
