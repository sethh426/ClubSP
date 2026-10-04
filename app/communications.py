"""Private conversation records and grounded drafts. No external email transport."""
from datetime import date
import hashlib
import json
import re
from uuid import UUID, uuid4

from core.memory.models import utc_now
from .operations import business_today
from .validation import text_field, list_field, property_exists
from .relationships import email_blocked


PAIN_POINTS = {
    "price": "Proceeds / price", "timing": "Timing", "convenience": "Convenience",
    "trust": "Trust / certainty", "condition": "Condition / repairs",
    "authority": "Decision participants / authority", "other": "Other stated concern",
}
MESSAGE_CATEGORIES = {
    "general", "price", "timing", "trust", "role", "authority", "condition",
    "finance", "paperwork", "access", "privacy", "stop", "wrong_person", "legal", "payment_change",
}
STOP_PATTERNS = (
    r"\bstop (contacting|emailing|calling|texting) me\b", r"\bdo not contact me\b", r"\bdon't contact me\b",
    r"\bremove me from (your|the) list\b", r"\bunsubscribe\b", r"\bwrong person\b",
    r"^\s*stop[.!]?\s*$",
)
REPLIES = {
    "general": "Thanks for getting back to me. What would a good outcome look like for you, and what timing would work best?",
    "price": "I understand that price is a concern. Is the main issue the amount, or are there terms that also matter to you?",
    "timing": "Thanks for explaining the timing concern. What date would work best, and is there any flexibility? Any proposed schedule would need a review of the remaining conditions.",
    "trust": "I understand wanting to verify who you are dealing with. What identity or process information would you like to review before continuing?",
    "authority": "Thanks for explaining. Who else needs to be involved in the decision, and how would you prefer to coordinate that review?",
    "condition": "What condition or repair issues should an evaluator understand? We can discuss an agreed evidence or access step before making assumptions about costs.",
    "access": "What access arrangements, if any, would you be comfortable with? We can discuss a permitted next step that respects occupants and privacy.",
    "privacy": "What information would you prefer to keep private? We can limit the discussion to what is needed for a next step you are comfortable with.",
    "finance": "What funding and review requirements would need to be satisfied before discussing an agreement? Interest and verified ability to close should be considered separately.",
    "role": "I will provide the actual proposed role and transaction structure in writing after the relevant details have been reviewed. You can review them independently before deciding whether to proceed.",
    "paperwork": "Which terms would you like the appropriate professional to explain? We can pause the decision while you obtain independent review.",
    "legal": "That question needs review for the actual proposal and jurisdiction. I will seek the appropriate professional review before making any commitment about it.",
    "payment_change": "I will verify any payment-instruction change independently through the established closing contact before taking action.",
}


def contact_exists(connection, contact_id):
    try:
        contact_id = str(UUID(str(contact_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("contact_id must be a UUID") from exc
    row = connection.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if row is None:
        raise LookupError("Contact not found")
    return row


def _insert(connection, table, record):
    connection.execute("INSERT INTO " + table + "(" + ",".join(record) + ") VALUES(" + ",".join("?" for _ in record) + ")", tuple(record.values()))


class CommunicationsMixin:
    def create_contact(self, data):
        name = text_field(data, "name", 120)
        email = text_field(data, "email", 254, required=False).lower()
        if email and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email):
            raise ValueError("Enter a valid email address without line breaks")
        role = text_field(data, "role", 30)
        if role not in {"unverified", "owner", "representative", "other"}:
            raise ValueError("Unsupported contact role")
        role_reference = text_field(data, "role_reference", 500, required=role in {"owner", "representative"})
        with self.database.session(write=True) as (connection, _):
            pid = str(property_exists(connection, data.get("property_id")))
            if email and connection.execute("SELECT id FROM contacts WHERE property_id=? AND email=?", (pid, email)).fetchone():
                raise ValueError("This property already has a contact with that email")
            suppressed = email_blocked(connection, email)
            now = utc_now().isoformat()
            record = {"id": str(uuid4()), "property_id": pid, "name": name, "email": email,
                      "role": role, "role_reference": role_reference,
                      "permission_status": "suppressed" if suppressed else "unknown",
                      "permission_reference": "Existing workspace suppression" if suppressed else "",
                      "created_at": now, "updated_at": now}
            _insert(connection, "contacts", record)
            return record

    def _set_permission(self, connection, contact, status, note, evidence):
        now = utc_now().isoformat()
        connection.execute("UPDATE contacts SET permission_status=?,permission_reference=?,updated_at=? WHERE id=?", (status, evidence, now, contact["id"]))
        _insert(connection, "contact_events", {"id": str(uuid4()), "contact_id": contact["id"],
                 "status_before": contact["permission_status"], "status_after": status,
                 "note": note, "evidence_reference": evidence, "created_at": now})
        if status == "suppressed":
            ids = [contact["id"]]
            if contact["email"]:
                connection.execute("INSERT OR IGNORE INTO suppressions(email,contact_id,reason,evidence_reference,created_at) VALUES(?,?,?,?,?)", (contact["email"], contact["id"], note, evidence, now))
                for sibling in connection.execute("SELECT * FROM contacts WHERE email=? AND id!=?", (contact["email"], contact["id"])).fetchall():
                    ids.append(sibling["id"])
                    connection.execute("UPDATE contacts SET permission_status='suppressed',permission_reference=?,updated_at=? WHERE id=?", (evidence, now, sibling["id"]))
                    _insert(connection, "contact_events", {"id": str(uuid4()), "contact_id": sibling["id"],
                        "status_before": sibling["permission_status"], "status_after": "suppressed",
                        "note": "Matching-email suppression: " + note, "evidence_reference": evidence, "created_at": now})
            for contact_id in ids:
                connection.execute("UPDATE reply_drafts SET status='void' WHERE contact_id=? AND status!='void'", (contact_id,))

    def set_contact_permission(self, contact_id, data):
        status = text_field(data, "status", 20)
        if status not in {"unknown", "permitted", "suppressed"}:
            raise ValueError("Unsupported permission status")
        note = text_field(data, "note", 1000)
        evidence = text_field(data, "evidence_reference", 500, required=status != "unknown")
        with self.database.session(write=True) as (connection, _):
            contact = contact_exists(connection, contact_id)
            if (contact["permission_status"] == "suppressed" or email_blocked(connection, contact["email"])) and status != "suppressed":
                raise ValueError("A recorded suppression cannot be cleared in this release")
            self._set_permission(connection, contact, status, note, evidence)
            return dict(connection.execute("SELECT * FROM contacts WHERE id=?", (contact["id"],)).fetchone())

    def record_message(self, contact_id, data):
        try:
            key = str(UUID(text_field(data, "message_key", 36)))
        except ValueError as exc:
            raise ValueError("message_key must be a UUID") from exc
        direction = text_field(data, "direction", 20)
        channel = text_field(data, "channel", 20)
        category = text_field(data, "category", 30)
        body = text_field(data, "body", 8000)
        evidence = text_field(data, "evidence_reference", 500)
        occurred = text_field(data, "occurred_on", 10)
        try:
            if date.fromisoformat(occurred).isoformat() != occurred or occurred > business_today().isoformat():
                raise ValueError("Noncanonical or future date")
        except ValueError as exc:
            raise ValueError("occurred_on must be a real YYYY-MM-DD date, not in the future") from exc
        if direction not in {"incoming", "outgoing", "note"} or channel not in {"email", "phone", "in_person", "note"} or category not in MESSAGE_CATEGORIES:
            raise ValueError("Unsupported conversation direction, channel or category")
        with self.database.session(write=True) as (connection, _):
            contact = contact_exists(connection, contact_id)
            existing = connection.execute("SELECT * FROM conversation_messages WHERE message_key=?", (key,)).fetchone()
            fields = ("contact_id", "direction", "channel", "category", "body", "evidence_reference", "occurred_on")
            payload = (contact["id"], direction, channel, category, body, evidence, occurred)
            if existing:
                if tuple(existing[f] for f in fields) != payload:
                    raise ValueError("message_key already records a different conversation")
                return dict(existing)
            record = dict(zip(fields, payload))
            record.update(id=str(uuid4()), message_key=key, created_at=utc_now().isoformat())
            _insert(connection, "conversation_messages", record)
            if direction == "incoming" and (category in {"stop", "wrong_person"} or any(re.search(pattern, body.replace("’", "'"), re.I) for pattern in STOP_PATTERNS)):
                self._set_permission(connection, contact, "suppressed", "Recorded stop/wrong-person request", "conversation_message:" + record["id"])
            return record

    def save_seller_profile(self, contact_id, data):
        status = text_field(data, "status", 20)
        if status not in {"confirmed", "hypothesis"}:
            raise ValueError("Profile must be confirmed or hypothesis")
        profile = {key: text_field(data, key, 2000, required=key == "goal") for key in ("goal", "timing", "condition_notes", "authority_notes", "alternatives", "priority")}
        points = list_field(data, "pain_points", allowed=PAIN_POINTS, required=False, max_items=7)
        evidence = text_field(data, "evidence_reference", 500, required=status == "confirmed")
        with self.database.session(write=True) as (connection, _):
            contact = contact_exists(connection, contact_id)
            record = {"id": str(uuid4()), "contact_id": contact["id"], "status": status, **profile,
                      "pain_points_json": json.dumps(points), "evidence_reference": evidence,
                      "created_at": utc_now().isoformat()}
            _insert(connection, "seller_profiles", record)
            record["pain_points"] = points
            del record["pain_points_json"]
            return record

    @staticmethod
    def _draft_context(connection, contact):
        latest = connection.execute("SELECT * FROM conversation_messages WHERE contact_id=? ORDER BY created_at DESC,id LIMIT 1", (contact["id"],)).fetchone()
        incoming = connection.execute("SELECT * FROM conversation_messages WHERE contact_id=? AND direction='incoming' ORDER BY created_at DESC,id LIMIT 1", (contact["id"],)).fetchone()
        profile = connection.execute("SELECT id FROM seller_profiles WHERE contact_id=? ORDER BY created_at DESC,id LIMIT 1", (contact["id"],)).fetchone()
        plans = [r["id"] for r in connection.execute("SELECT f.id FROM financial_plans f JOIN deals d ON d.id=f.deal_id WHERE d.property_id=? ORDER BY f.created_at,f.id", (contact["property_id"],))]
        underwritings = [r["id"] for r in connection.execute("SELECT u.id FROM underwritings u JOIN deals d ON d.id=u.deal_id WHERE d.property_id=? ORDER BY u.created_at,u.id", (contact["property_id"],))]
        stages = [tuple(r) for r in connection.execute("SELECT id,stage FROM deals WHERE property_id=? ORDER BY id", (contact["property_id"],))]
        knowledge_ids = [r["id"] for r in connection.execute("SELECT id FROM knowledge_items WHERE status='active' ORDER BY id")]
        context = {"contact_id": contact["id"], "contact_updated_at": contact["updated_at"],
                   "latest_message_id": latest["id"] if latest else None,
                   "incoming_message_id": incoming["id"] if incoming else None,
                   "profile_id": profile["id"] if profile else None, "plan_ids": plans,
                   "underwriting_ids": underwritings, "deal_stages": stages, "knowledge_item_ids": knowledge_ids}
        digest = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
        return context, digest, incoming

    def suggest_reply(self, contact_id, data):
        if data != {}:
            raise ValueError("Reply suggestion uses only saved conversation context")
        with self.database.session(write=True) as (connection, _):
            contact = contact_exists(connection, contact_id)
            if contact["permission_status"] == "suppressed":
                raise ValueError("Contact is suppressed; no sales reply is generated")
            context, digest, incoming = self._draft_context(connection, contact)
            if incoming is None:
                raise ValueError("Record an incoming conversation before requesting a reply")
            latest_outgoing = connection.execute("SELECT created_at FROM conversation_messages WHERE contact_id=? AND direction='outgoing' ORDER BY created_at DESC,id LIMIT 1", (contact["id"],)).fetchone()
            if latest_outgoing and latest_outgoing["created_at"] > incoming["created_at"]:
                raise ValueError("A later outgoing conversation is already recorded; wait for a new incoming reply")
            category = incoming["category"]
            if category in {"stop", "wrong_person"}:
                raise ValueError("No sales reply after a stop or wrong-person request")
            existing = connection.execute("SELECT * FROM reply_drafts WHERE contact_id=? AND context_hash=? AND status!='void' ORDER BY created_at DESC,id LIMIT 1", (contact["id"], digest)).fetchone()
            if existing:
                return self._draft_json(connection, existing, contact)
            prop = connection.execute("SELECT address FROM properties WHERE id=?", (contact["property_id"],)).fetchone()
            body = REPLIES[category]
            if category == "price" and context["underwriting_ids"]:
                body += " I can explain my recorded assumptions after reviewing them against the available evidence."
            record = {"id": str(uuid4()), "contact_id": contact["id"], "message_id": incoming["id"],
                      "context_hash": digest, "context_json": json.dumps(context), "category": category,
                      "subject": "Re: " + " ".join(prop["address"].split()), "body": body,
                      "status": "draft", "final_body": "", "review_note": "", "reviewed_at": None,
                      "created_at": utc_now().isoformat()}
            _insert(connection, "reply_drafts", record)
            return self._draft_json(connection, record, contact)

    def _draft_json(self, connection, row, contact):
        record = dict(row)
        record["context"] = json.loads(record.pop("context_json"))
        _, current_hash, _ = self._draft_context(connection, contact)
        record["current"] = record["context_hash"] == current_hash and record["status"] != "void"
        record["external_send_available"] = False
        record["review_blockers"] = []
        if not record["current"]:
            record["review_blockers"].append("Conversation, profile, deal or terms changed; generate a current draft")
        if contact["permission_status"] != "permitted" or email_blocked(connection, contact["email"]):
            record["review_blockers"].append("Contact permission requires an owner evidence record")
        return record

    def review_reply(self, draft_id, data):
        if data.get("owner_reviewed") is not True:
            raise ValueError("Owner review is required")
        body = text_field(data, "final_body", 8000)
        note = text_field(data, "note", 1000)
        with self.database.session(write=True) as (connection, _):
            row = connection.execute("SELECT * FROM reply_drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                raise LookupError("Reply draft not found")
            contact = contact_exists(connection, row["contact_id"])
            draft = self._draft_json(connection, row, contact)
            if draft["review_blockers"]:
                raise ValueError("; ".join(draft["review_blockers"]))
            if row["status"] == "reviewed":
                if (row["final_body"], row["review_note"]) != (body, note):
                    raise ValueError("Reviewed draft is immutable; regenerate after a new conversation record")
                return draft
            connection.execute("UPDATE reply_drafts SET status='reviewed',final_body=?,review_note=?,reviewed_at=? WHERE id=?", (body, note, utc_now().isoformat(), draft_id))
            return self._draft_json(connection, connection.execute("SELECT * FROM reply_drafts WHERE id=?", (draft_id,)).fetchone(), contact)

    def _communications_state(self, connection):
        contacts = []
        counts = {key: 0 for key in PAIN_POINTS}
        confirmed = hypotheses = 0
        for row in connection.execute("SELECT * FROM contacts ORDER BY created_at DESC,id"):
            contact = dict(row)
            contact["events"] = [dict(r) for r in connection.execute("SELECT * FROM contact_events WHERE contact_id=? ORDER BY created_at,id", (contact["id"],))]
            contact["messages"] = [dict(r) for r in connection.execute("SELECT * FROM conversation_messages WHERE contact_id=? ORDER BY created_at,id", (contact["id"],))]
            contact["profiles"] = []
            for r in connection.execute("SELECT * FROM seller_profiles WHERE contact_id=? ORDER BY created_at DESC,id", (contact["id"],)):
                profile = dict(r)
                profile["pain_points"] = json.loads(profile.pop("pain_points_json"))
                contact["profiles"].append(profile)
            current = contact["profiles"][0] if contact["profiles"] else None
            if current:
                if current["status"] == "confirmed":
                    confirmed += 1
                    for point in current["pain_points"]:
                        counts[point] += 1
                else:
                    hypotheses += 1
            contact["drafts"] = [self._draft_json(connection, r, contact) for r in connection.execute("SELECT * FROM reply_drafts WHERE contact_id=? ORDER BY created_at DESC,id", (contact["id"],))]
            contacts.append(contact)
        return {"contacts": contacts, "pain_point_labels": PAIN_POINTS,
                "message_categories": sorted(MESSAGE_CATEGORIES), "transport": "Not configured; drafts only",
                "insights": {"contacts": len(contacts), "confirmed_profiles": confirmed,
                             "hypotheses": hypotheses, "unknown_profiles": len(contacts) - confirmed - hypotheses,
                             "confirmed_pain_points": counts,
                             "method": "Unique contacts, latest profile only; owner-confirmed statements, not market prevalence or causality"}}
