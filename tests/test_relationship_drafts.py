from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.relationships import RelationshipBook
from app.service import Application
from tests.test_communications import setup_contact, message
from tests.test_relationships import setup, profile, current, interaction, revise


def draft_data(row, **changes):
    return {"request_key": str(uuid4()), "profile_id": row["profile"]["id"], "event_id": row["event_id"] or "",
            "draft_id": row["saved_drafts"][0]["id"] if row["saved_drafts"] else "",
            "subject": "Synthetic introduction", "body": "Synthetic message only. What areas are you considering?", **changes}


def review_data(draft, **changes):
    return {"request_key": str(uuid4()), "draft_id": draft["id"], "review_id": draft["reviews"][0]["id"] if draft["reviews"] else "",
            "decision": "approved", "reviewer": "Synthetic owner", "note": "Synthetic identity, text and permission review", **changes}


def ready(tmp_path):
    app, book = setup(tmp_path)
    book.save(profile(permission="owner_reviewed", permission_reference="synthetic"))
    row = current(book)
    book.save_draft(row["id"], draft_data(row))
    return app, book, current(book)


def test_draft_review_persists_exact_text_and_recipient_without_sending(tmp_path):
    app, book, row = ready(tmp_path)
    draft = row["saved_drafts"][0]
    assert draft["review_status"] == "pending"
    book.review_draft(row["id"], review_data(draft))
    restored = current(RelationshipBook(Application(app.database.path)))
    saved = restored["saved_drafts"][0]
    assert saved["body"] == draft["body"] and saved["recipient"] == row["profile"]["email"]
    assert saved["review_status"] == "approved" and not saved["sending_enabled"]
    assert saved["reviews"][0]["note"] == "Synthetic identity, text and permission review"


def test_retries_are_idempotent_and_conflicting_edits_cannot_overwrite(tmp_path):
    _, book, row = ready(tmp_path)
    data = draft_data(row, body="Synthetic second version")
    with ThreadPoolExecutor(max_workers=3) as pool:
        drafts = list(pool.map(lambda _: book.save_draft(row["id"], data), range(3)))
    assert len({d["id"] for d in drafts}) == 1
    with pytest.raises(ValueError, match="different draft"):
        book.save_draft(row["id"], {**data, "body": "Changed retry"})
    with pytest.raises(ValueError, match="Draft changed"):
        book.save_draft(row["id"], draft_data(row))
    row = current(book)
    data = review_data(row["saved_drafts"][0])
    with ThreadPoolExecutor(max_workers=3) as pool:
        reviews = list(pool.map(lambda _: book.review_draft(row["id"], data), range(3)))
    assert len({r["id"] for r in reviews}) == 1
    with pytest.raises(ValueError, match="Review changed"):
        book.review_draft(row["id"], {**data, "request_key": str(uuid4()), "decision": "rejected"})


@pytest.mark.parametrize("change", ["profile", "conversation", "new_draft", "paused", "stop"])
def test_changes_invalidate_approval_and_preserve_review_history(tmp_path, change):
    _, book, row = ready(tmp_path)
    old = row["saved_drafts"][0]
    book.review_draft(row["id"], review_data(old))
    if change == "profile":
        revise(book, current(book), email="changed@example.test")
    elif change == "paused":
        revise(book, current(book), status="paused")
    elif change in {"conversation", "stop"}:
        book.interact(row["id"], interaction(current(book), outcome="stop" if change == "stop" else "general"))
    else:
        book.save_draft(row["id"], draft_data(current(book), body="New synthetic text"))
    row = current(book)
    old_now = next(d for d in row["saved_drafts"] if d["id"] == old["id"])
    assert old_now["review_status"] == "blocked" and old_now["reviews"][0]["decision"] == "approved"
    assert old_now["recipient"] == "investor@example.test"
    with pytest.raises(ValueError):
        book.review_draft(row["id"], review_data(old_now))
    if change == "new_draft":
        assert row["saved_drafts"][0]["review_status"] == "pending"


def test_property_suppression_also_invalidates_relationship_approval(tmp_path):
    app, _, contact = setup_contact(tmp_path)
    book = RelationshipBook(app)
    book.save(profile(email=contact["email"], permission="owner_reviewed", permission_reference="synthetic"))
    row = current(book)
    book.save_draft(row["id"], draft_data(row))
    row = current(book)
    book.review_draft(row["id"], review_data(row["saved_drafts"][0]))
    app.record_message(contact["id"], message(app, contact, body="Unsubscribe"))
    row = current(book)
    assert row["saved_drafts"][0]["review_status"] == "blocked"
    with pytest.raises(ValueError, match="Do not contact"):
        book.save_draft(row["id"], draft_data(row))


@pytest.mark.parametrize("changes", [{"subject": ""}, {"subject": "Hi\nBcc: another@example.test"}, {"body": ""}, {"body": "x"*8001}, {"subject": "x"*201}])
def test_invalid_draft_keeps_existing_text(tmp_path, changes):
    _, book, row = ready(tmp_path)
    with pytest.raises(ValueError):
        book.save_draft(row["id"], draft_data(row, **changes))
    assert len(current(book)["saved_drafts"]) == 1


@pytest.mark.parametrize("changes", [{"permission": "unknown"}, {"status": "closed"}, {"email": ""}, {"permission": "blocked"}])
def test_draft_creation_requires_eligible_relationship(tmp_path, changes):
    _, book = setup(tmp_path)
    book.save(profile(**{"permission": "owner_reviewed", "permission_reference": "synthetic", **changes}))
    row = current(book)
    with pytest.raises(ValueError):
        book.save_draft(row["id"], draft_data(row))
    assert current(book)["saved_drafts"] == []


def test_review_belongs_to_relationship_and_rejection_does_not_approve(tmp_path):
    _, book, row = ready(tmp_path)
    book.save(profile(email="other@example.test"))
    other = next(r for r in book.state()["relationships"] if r["id"] != row["id"])
    with pytest.raises(LookupError):
        book.review_draft(other["id"], review_data(row["saved_drafts"][0]))
    book.review_draft(row["id"], review_data(row["saved_drafts"][0], decision="rejected"))
    row = current(book, row["id"])
    assert row["saved_drafts"][0]["review_status"] == "rejected"
    book.review_draft(row["id"], review_data(row["saved_drafts"][0]))
    assert len(current(book, row["id"])["saved_drafts"][0]["reviews"]) == 2


def test_stale_draft_editor_cannot_save_after_reply_and_changed_review_retry_fails(tmp_path):
    _, book, row = ready(tmp_path)
    data = review_data(row["saved_drafts"][0])
    book.review_draft(row["id"], data)
    with pytest.raises(ValueError, match="different review"):
        book.review_draft(row["id"], {**data, "decision": "rejected"})
    book.interact(row["id"], interaction(row))
    with pytest.raises(ValueError, match="Relationship changed"):
        book.save_draft(row["id"], draft_data(row))
    assert len(current(book)["saved_drafts"]) == 1
