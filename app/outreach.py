"""One-at-a-time, owner-approved relationship outreach over Gmail."""
from __future__ import annotations

import json
from uuid import UUID, uuid4

from core.memory.models import utc_now
from .operations import business_today
from .relationships import day, identifier, text_field


class ApprovedOutreach:
    def __init__(self, relationship_book, gmail):
        self.book = relationship_book
        self.database = relationship_book.database
        self.gmail = gmail
        with self.database.session(write=True) as (connection, _):
            connection.execute("""
                CREATE TABLE IF NOT EXISTS relationship_sends (
                    id TEXT PRIMARY KEY,
                    relationship_id TEXT NOT NULL REFERENCES relationships(id),
                    draft_id TEXT NOT NULL REFERENCES relationship_drafts(id),
                    review_id TEXT NOT NULL REFERENCES relationship_draft_reviews(id),
                    profile_id TEXT NOT NULL REFERENCES relationship_profiles(id),
                    previous_event_id TEXT REFERENCES relationship_interactions(id),
                    request_key TEXT NOT NULL UNIQUE,
                    recipient TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    follow_up_on TEXT NOT NULL,
                    next_action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    provider_message_id TEXT NOT NULL,
                    provider_thread_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                )
            """)
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS relationship_send_draft_once "
                "ON relationship_sends(draft_id) WHERE status IN ('pending','sent','uncertain')"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS relationship_sends_relationship "
                "ON relationship_sends(relationship_id,created_at)"
            )

    @staticmethod
    def _row(row):
        return dict(row) if row else None

    def _same_request(self, row, expected):
        return all(row[key] == value for key, value in expected.items())

    def prepare(self, rid, data):
        request_key = identifier(data, "request_key")
        draft_id = identifier(data, "draft_id")
        review_id = identifier(data, "review_id")
        follow_up_on = day(data, "follow_up_on", required=True)
        next_action = text_field(data, "next_action", 500)
        if data.get("owner_confirmed_send") is not True:
            raise ValueError("Explicit owner send confirmation is required")
        now = utc_now().isoformat()
        with self.database.session(write=True) as (connection, _):
            existing = connection.execute(
                "SELECT * FROM relationship_sends WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing:
                expected = {"relationship_id": rid, "draft_id": draft_id, "review_id": review_id,
                            "follow_up_on": follow_up_on, "next_action": next_action}
                if not self._same_request(existing, expected):
                    raise ValueError("request_key already records a different send")
                return self._row(existing)

            profile_row = self.book.latest(connection, "relationship_profiles", rid)
            if not profile_row:
                raise LookupError("Relationship not found")
            profile = self.book.decode(profile_row)
            event = self.book.latest(connection, "relationship_interactions", rid)
            draft = connection.execute(
                "SELECT * FROM relationship_drafts WHERE id=? AND relationship_id=?", (draft_id, rid)
            ).fetchone()
            if not draft:
                raise LookupError("Draft not found for this relationship")
            latest_draft = self.book.latest(connection, "relationship_drafts", rid)
            if not latest_draft or latest_draft["id"] != draft_id:
                raise ValueError("A newer draft exists; review the current draft before sending")
            review = connection.execute(
                "SELECT * FROM relationship_draft_reviews WHERE id=? AND draft_id=?", (review_id, draft_id)
            ).fetchone()
            if not review:
                raise LookupError("Approved review not found for this draft")
            latest_review = connection.execute(
                "SELECT * FROM relationship_draft_reviews WHERE draft_id=? ORDER BY rowid DESC LIMIT 1",
                (draft_id,),
            ).fetchone()
            if not latest_review or latest_review["id"] != review_id:
                raise ValueError("Draft review changed; refresh before sending")
            decoded_review = self.book.decode(review)
            if decoded_review["decision"] != "approved":
                raise ValueError("The current draft review is not approved")
            blockers = self.book.draft_blockers(connection, rid, profile, draft)
            if blockers:
                raise ValueError("; ".join(blockers))
            if draft["profile_id"] != profile["id"] or draft["event_id"] != (event["id"] if event else None):
                raise ValueError("Relationship changed; save and approve a new draft")
            prior = connection.execute(
                "SELECT status FROM relationship_sends WHERE draft_id=? AND status IN ('pending','sent','uncertain')",
                (draft_id,),
            ).fetchone()
            if prior:
                raise ValueError("This approved draft already has a send attempt; refresh before taking another action")
            message = self.book.decode(draft)
            send_id = str(uuid4())
            connection.execute(
                "INSERT INTO relationship_sends VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (send_id, rid, draft_id, review_id, profile["id"], event["id"] if event else None,
                 request_key, profile["email"], message["subject"], message["body"],
                 follow_up_on, next_action, "pending", "", "", now, ""),
            )
            return self._row(connection.execute(
                "SELECT * FROM relationship_sends WHERE id=?", (send_id,)
            ).fetchone())

    def mark_uncertain(self, send_id):
        with self.database.session(write=True) as (connection, _):
            connection.execute(
                "UPDATE relationship_sends SET status='uncertain',completed_at=? "
                "WHERE id=? AND status='pending'", (utc_now().isoformat(), send_id)
            )

    def complete(self, send_id, receipt):
        completed = utc_now().isoformat()
        occurred_on = business_today().isoformat()
        with self.database.session(write=True) as (connection, _):
            row = connection.execute("SELECT * FROM relationship_sends WHERE id=?", (send_id,)).fetchone()
            if not row:
                raise LookupError("Send record not found")
            if row["status"] == "sent":
                return self._row(row)
            if row["status"] != "pending":
                raise ValueError("Send record is not pending")
            connection.execute(
                "UPDATE relationship_sends SET status='sent',provider_message_id=?,provider_thread_id=?,completed_at=? "
                "WHERE id=? AND status='pending'",
                (receipt["message_id"], receipt["thread_id"], completed, send_id),
            )
            payload = {
                "direction": "outgoing", "outcome": "general",
                "note": "Sent owner-approved Gmail draft: " + row["subject"],
                "evidence_reference": "gmail_message:" + receipt["message_id"],
                "occurred_on": occurred_on, "follow_up_on": row["follow_up_on"],
                "next_action": row["next_action"],
            }
            connection.execute(
                "INSERT INTO relationship_interactions VALUES(?,?,?,?,?,?,?)",
                (str(uuid4()), row["relationship_id"], "send:" + row["request_key"],
                 row["profile_id"], row["previous_event_id"], json.dumps(payload, sort_keys=True), completed),
            )
            return self._row(connection.execute(
                "SELECT * FROM relationship_sends WHERE id=?", (send_id,)
            ).fetchone())

    def send(self, rid, data):
        record = self.prepare(rid, data)
        if record["status"] == "sent":
            return record
        if record["status"] != "pending":
            raise ValueError("This send attempt is not eligible to run")
        try:
            receipt = self.gmail.send_message(
                record["recipient"], record["subject"], record["body"], record["request_key"]
            )
        except Exception:
            self.mark_uncertain(record["id"])
            raise ValueError(
                "Gmail send result is uncertain. Check the Sent mailbox before attempting another message."
            ) from None
        return self.complete(record["id"], receipt)

    def decorate_state(self, state, sending_enabled):
        with self.database.session() as (connection, _):
            sends = connection.execute(
                "SELECT * FROM relationship_sends ORDER BY rowid DESC"
            ).fetchall()
            by_draft = {}
            for row in sends:
                by_draft.setdefault(row["draft_id"], self._row(row))
        for relationship in state["relationships"]:
            for draft in relationship["saved_drafts"]:
                send = by_draft.get(draft["id"])
                draft["send"] = send
                latest_review = draft["reviews"][0] if draft["reviews"] else None
                draft["send_enabled"] = bool(
                    sending_enabled and not draft["review_blockers"]
                    and latest_review and latest_review["decision"] == "approved"
                    and not send
                )
        state["sending_enabled"] = bool(sending_enabled)
        state["send_mode"] = "explicit_owner_action"
        return state
