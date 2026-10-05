"""Property-independent relationships and owner-entered follow-ups; no transport."""
from datetime import date
import json
import re
from uuid import UUID, uuid4

from core.memory.models import utc_now
from .operations import business_today
from .validation import list_field, text_field


def identifier(data, key, required=True):
    value = text_field(data, key, 36, required=required)
    if not value:
        return None
    try:
        return str(UUID(value))
    except ValueError:
        raise ValueError(f"{key} must be a UUID") from None


def day(data, key, required=False, past=False):
    value = text_field(data, key, 10, required=required)
    if not value:
        return ""
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value or (past and parsed > business_today()):
            raise ValueError()
    except ValueError:
        raise ValueError(f"{key} must be YYYY-MM-DD" + (", not in the future" if past else "")) from None
    return value


def email_blocked(connection, email):
    if not email:
        return False
    if connection.execute("SELECT 1 FROM suppressions WHERE email=?", (email,)).fetchone():
        return True
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='relationship_stops'").fetchone()
    return bool(exists and connection.execute("SELECT 1 FROM relationship_stops WHERE email=?", (email,)).fetchone())


class RelationshipBook:
    def __init__(self, application):
        self.application = application
        self.database = application.database
        with self.database.session(write=True) as (connection, _):
            statements = [
                "CREATE TABLE IF NOT EXISTS relationships (id TEXT PRIMARY KEY, created_at TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS relationship_profiles (id TEXT PRIMARY KEY, relationship_id TEXT NOT NULL REFERENCES relationships(id), previous_id TEXT UNIQUE REFERENCES relationship_profiles(id), request_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS relationship_profile_lookup ON relationship_profiles(relationship_id)",
                "CREATE TABLE IF NOT EXISTS relationship_interactions (id TEXT PRIMARY KEY, relationship_id TEXT NOT NULL REFERENCES relationships(id), request_key TEXT NOT NULL UNIQUE, profile_id TEXT NOT NULL REFERENCES relationship_profiles(id), previous_event_id TEXT REFERENCES relationship_interactions(id), payload TEXT NOT NULL, created_at TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS relationship_interaction_lookup ON relationship_interactions(relationship_id)",
                "CREATE TABLE IF NOT EXISTS relationship_stops (id TEXT PRIMARY KEY, relationship_id TEXT NOT NULL REFERENCES relationships(id), email TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES relationship_interactions(id), created_at TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS relationship_drafts (id TEXT PRIMARY KEY, relationship_id TEXT NOT NULL REFERENCES relationships(id), profile_id TEXT NOT NULL REFERENCES relationship_profiles(id), event_id TEXT REFERENCES relationship_interactions(id), previous_id TEXT UNIQUE REFERENCES relationship_drafts(id), request_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS relationship_draft_lookup ON relationship_drafts(relationship_id)",
                "CREATE TABLE IF NOT EXISTS relationship_draft_reviews (id TEXT PRIMARY KEY, draft_id TEXT NOT NULL REFERENCES relationship_drafts(id), request_key TEXT NOT NULL UNIQUE, previous_id TEXT UNIQUE REFERENCES relationship_draft_reviews(id), payload TEXT NOT NULL, created_at TEXT NOT NULL)",
            ]
            for statement in statements:
                connection.execute(statement)

    @staticmethod
    def decode(row):
        result = dict(row)
        result.update(json.loads(result.pop("payload")))
        return result

    @staticmethod
    def latest(connection, table, rid):
        return connection.execute(f"SELECT * FROM {table} WHERE relationship_id=? ORDER BY rowid DESC LIMIT 1", (rid,)).fetchone()

    def context(self, connection, rid, profile_id, event_id):
        profile = self.latest(connection, "relationship_profiles", rid)
        if not profile:
            raise LookupError("Relationship not found")
        event = self.latest(connection, "relationship_interactions", rid)
        if profile_id != profile["id"] or event_id != (event["id"] if event else None):
            raise ValueError("Relationship changed; refresh before saving")
        return profile

    def save(self, data):
        key = identifier(data, "request_key")
        rid = identifier(data, "relationship_id", False)
        previous = identifier(data, "profile_id", False)
        event_id = identifier(data, "event_id", False)
        payload = {name: text_field(data, name, limit, required=required) for name, limit, required in [
            ("name", 120, True), ("company", 160, False), ("email", 254, False),
            ("kind", 20, True), ("status", 20, True), ("needs", 2000, False),
            ("source_reference", 500, True), ("permission", 20, True),
            ("permission_reference", 500, False), ("next_action", 500, False), ("owner", 120, True),
        ]}
        payload["email"] = payload["email"].lower()
        if payload["email"] and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", payload["email"]):
            raise ValueError("Enter an email address without whitespace or headers")
        if payload["kind"] not in {"investor", "agent", "closing_partner", "other"}:
            raise ValueError("Unsupported relationship kind")
        if payload["status"] not in {"prospect", "engaged", "active", "paused", "closed"}:
            raise ValueError("Unsupported relationship status")
        if payload["permission"] not in {"unknown", "owner_reviewed", "blocked"}:
            raise ValueError("Unsupported permission review")
        if payload["permission"] == "owner_reviewed" and not payload["permission_reference"]:
            raise ValueError("Record a permission review reference")
        payload["markets"] = list_field(data, "markets", required=False, max_items=20)
        payload["buyer_id"] = identifier(data, "buyer_id", False)
        payload["follow_up_on"] = day(data, "follow_up_on")
        if payload["follow_up_on"] and not payload["next_action"]:
            raise ValueError("A scheduled follow-up needs a next action")
        payload["event_id_at_review"] = event_id
        encoded = json.dumps(payload, sort_keys=True)
        with self.database.session(write=True) as (connection, _):
            existing = connection.execute("SELECT * FROM relationship_profiles WHERE request_key=?", (key,)).fetchone()
            if existing:
                if existing["payload"] != encoded or existing["previous_id"] != previous or (rid and rid != existing["relationship_id"]):
                    raise ValueError("request_key already records different profile data")
                return self.decode(existing)
            if rid:
                self.context(connection, rid, previous, event_id)
            elif previous or event_id:
                raise ValueError("New relationships cannot supersede existing history")
            if payload["email"]:
                profiles = connection.execute("SELECT p.payload,p.relationship_id FROM relationship_profiles p WHERE p.rowid IN (SELECT MAX(rowid) FROM relationship_profiles GROUP BY relationship_id)").fetchall()
                if any(json.loads(p["payload"])["email"] == payload["email"] and p["relationship_id"] != rid for p in profiles):
                    raise ValueError("This email already has a relationship; update that record")
            if payload["buyer_id"] and not connection.execute("SELECT 1 FROM buyers WHERE id=?", (payload["buyer_id"],)).fetchone():
                raise LookupError("Linked buyer not found")
            stopped = rid and connection.execute("SELECT 1 FROM relationship_stops WHERE relationship_id=?", (rid,)).fetchone()
            if (stopped or email_blocked(connection, payload["email"])) and payload["permission"] == "owner_reviewed":
                raise ValueError("Recorded suppression cannot be cleared in this release")
            if not rid:
                rid = str(uuid4())
                connection.execute("INSERT INTO relationships VALUES(?,?)", (rid, utc_now().isoformat()))
            pid = str(uuid4())
            connection.execute("INSERT INTO relationship_profiles VALUES(?,?,?,?,?,?)", (pid, rid, previous, key, encoded, utc_now().isoformat()))
            return self.decode(connection.execute("SELECT * FROM relationship_profiles WHERE id=?", (pid,)).fetchone())

    def interact(self, rid, data):
        key = identifier(data, "request_key")
        profile_id = identifier(data, "profile_id")
        event_id = identifier(data, "event_id", False)
        payload = {name: text_field(data, name, limit) for name, limit in [
            ("direction", 20), ("outcome", 30), ("note", 4000), ("evidence_reference", 500)]}
        if payload["direction"] not in {"incoming", "outgoing", "note"} or payload["outcome"] not in {"general", "interested", "not_interested", "stop", "wrong_person"}:
            raise ValueError("Unsupported interaction direction or outcome")
        payload["occurred_on"] = day(data, "occurred_on", required=True, past=True)
        payload["follow_up_on"] = day(data, "follow_up_on")
        payload["next_action"] = text_field(data, "next_action", 500, required=bool(payload["follow_up_on"]))
        if payload["follow_up_on"] and payload["follow_up_on"] < payload["occurred_on"]:
            raise ValueError("Follow-up cannot predate the interaction")
        encoded = json.dumps(payload, sort_keys=True)
        with self.database.session(write=True) as (connection, _):
            existing = connection.execute("SELECT * FROM relationship_interactions WHERE request_key=?", (key,)).fetchone()
            if existing:
                if (existing["relationship_id"], existing["profile_id"], existing["previous_event_id"], existing["payload"]) != (rid, profile_id, event_id, encoded):
                    raise ValueError("request_key already records a different interaction")
                return self.decode(existing)
            profile = self.decode(self.context(connection, rid, profile_id, event_id))
            last = self.latest(connection, "relationship_interactions", rid)
            if last and payload["occurred_on"] < json.loads(last["payload"])["occurred_on"]:
                raise ValueError("Record interactions in chronological order")
            eid = str(uuid4())
            connection.execute("INSERT INTO relationship_interactions VALUES(?,?,?,?,?,?,?)", (eid, rid, key, profile_id, event_id, encoded, utc_now().isoformat()))
            stop = payload["outcome"] in {"stop", "wrong_person"} or (payload["direction"] == "incoming" and re.search(r"\b(unsubscribe|do not contact|don't contact|stop contacting|stop emailing|remove me|wrong person)\b|^\s*stop[.!]?\s*$", payload["note"].replace("’", "'"), re.I))
            if stop:
                connection.execute("INSERT INTO relationship_stops VALUES(?,?,?,?,?)", (str(uuid4()), rid, profile["email"], eid, utc_now().isoformat()))
                if profile["email"]:
                    for contact in connection.execute("SELECT * FROM contacts WHERE email=?", (profile["email"],)).fetchall():
                        self.application._set_permission(connection, contact, "suppressed", "Relationship stop/wrong-person record", "relationship_interaction:" + eid)
            return self.decode(connection.execute("SELECT * FROM relationship_interactions WHERE id=?", (eid,)).fetchone())

    def draft_blockers(self, connection, rid, profile, draft=None):
        blockers = []
        event = self.latest(connection, "relationship_interactions", rid)
        if draft:
            latest = self.latest(connection, "relationship_drafts", rid)
            if latest["id"] != draft["id"]:
                blockers.append("A newer draft exists")
            if draft["profile_id"] != profile["id"] or draft["event_id"] != (event["id"] if event else None):
                blockers.append("Relationship or conversation changed; save a new draft")
        stopped = connection.execute("SELECT 1 FROM relationship_stops WHERE relationship_id=?", (rid,)).fetchone()
        if stopped or email_blocked(connection, profile["email"]) or profile["permission"] == "blocked":
            blockers.append("Do not contact")
        if profile["status"] in {"paused", "closed"}:
            blockers.append("Relationship is paused or closed")
        if not profile["email"] or profile["permission"] != "owner_reviewed":
            blockers.append("Email and owner permission review required")
        return blockers

    def save_draft(self, rid, data):
        key = identifier(data, "request_key")
        profile_id = identifier(data, "profile_id")
        event_id = identifier(data, "event_id", False)
        previous = identifier(data, "draft_id", False)
        payload = {"subject": text_field(data, "subject", 200), "body": text_field(data, "body", 8000)}
        if "\r" in payload["subject"] or "\n" in payload["subject"]:
            raise ValueError("Subject must be a single line")
        encoded = json.dumps(payload, sort_keys=True)
        with self.database.session(write=True) as (connection, _):
            existing = connection.execute("SELECT * FROM relationship_drafts WHERE request_key=?", (key,)).fetchone()
            if existing:
                if (existing["relationship_id"], existing["profile_id"], existing["event_id"], existing["previous_id"], existing["payload"]) != (rid, profile_id, event_id, previous, encoded):
                    raise ValueError("request_key already records different draft data")
                return self.decode(existing)
            profile = self.decode(self.context(connection, rid, profile_id, event_id))
            last = self.latest(connection, "relationship_drafts", rid)
            if previous != (last["id"] if last else None):
                raise ValueError("Draft changed; refresh before saving")
            blockers = self.draft_blockers(connection, rid, profile)
            if blockers:
                raise ValueError("; ".join(blockers))
            did = str(uuid4())
            connection.execute("INSERT INTO relationship_drafts VALUES(?,?,?,?,?,?,?,?)", (did, rid, profile_id, event_id, previous, key, encoded, utc_now().isoformat()))
            return self.decode(connection.execute("SELECT * FROM relationship_drafts WHERE id=?", (did,)).fetchone())

    def review_draft(self, rid, data):
        key = identifier(data, "request_key")
        did = identifier(data, "draft_id")
        previous = identifier(data, "review_id", False)
        payload = {name: text_field(data, name, limit) for name, limit in [("decision", 20), ("reviewer", 120), ("note", 2000)]}
        if payload["decision"] not in {"approved", "rejected"}:
            raise ValueError("Review decision must be approved or rejected")
        encoded = json.dumps(payload, sort_keys=True)
        with self.database.session(write=True) as (connection, _):
            draft = connection.execute("SELECT * FROM relationship_drafts WHERE id=? AND relationship_id=?", (did, rid)).fetchone()
            if not draft:
                raise LookupError("Draft not found for this relationship")
            existing = connection.execute("SELECT * FROM relationship_draft_reviews WHERE request_key=?", (key,)).fetchone()
            if existing:
                if (existing["draft_id"], existing["previous_id"], existing["payload"]) != (did, previous, encoded):
                    raise ValueError("request_key already records a different review")
                return self.decode(existing)
            last = connection.execute("SELECT * FROM relationship_draft_reviews WHERE draft_id=? ORDER BY rowid DESC LIMIT 1", (did,)).fetchone()
            if previous != (last["id"] if last else None):
                raise ValueError("Review changed; refresh before reviewing")
            profile = self.decode(self.latest(connection, "relationship_profiles", rid))
            blockers = self.draft_blockers(connection, rid, profile, draft)
            if payload["decision"] == "approved" and blockers:
                raise ValueError("; ".join(blockers))
            review_id = str(uuid4())
            connection.execute("INSERT INTO relationship_draft_reviews VALUES(?,?,?,?,?,?)", (review_id, did, key, previous, encoded, utc_now().isoformat()))
            return self.decode(connection.execute("SELECT * FROM relationship_draft_reviews WHERE id=?", (review_id,)).fetchone())

    def state(self):
        today = business_today().isoformat()
        with self.database.session() as (connection, _):
            profiles = connection.execute("SELECT * FROM relationship_profiles ORDER BY rowid DESC").fetchall()
            events = connection.execute("SELECT * FROM relationship_interactions ORDER BY rowid DESC").fetchall()
            grouped, histories = {}, {}
            for row in profiles:
                histories.setdefault(row["relationship_id"], []).append(self.decode(row))
            for row in events:
                grouped.setdefault(row["relationship_id"], []).append(self.decode(row))
            records = []
            for rid, history in histories.items():
                profile = history[0]
                interactions = grouped.get(rid, [])
                last = interactions[0] if interactions else None
                # An interaction replaces the prior schedule. A newer profile explicitly re-schedules it.
                schedule = last if last and profile["event_id_at_review"] != last["id"] else profile
                stopped = bool(connection.execute("SELECT 1 FROM relationship_stops WHERE relationship_id=?", (rid,)).fetchone())
                blocked = stopped or email_blocked(connection, profile["email"]) or profile["permission"] == "blocked"
                paused = profile["status"] in {"paused", "closed"}
                due = schedule["follow_up_on"]
                eligible = not blocked and not paused
                buyer = connection.execute("SELECT id,name,status FROM buyers WHERE id=?", (profile["buyer_id"],)).fetchone() if profile["buyer_id"] else None
                draft = None
                if eligible and profile["email"] and profile["permission"] == "owner_reviewed":
                    area = ", ".join(profile["markets"]) or "your preferred areas"
                    draft = {"subject": "Your current buying priorities" if profile["kind"] == "investor" else "A possible working relationship",
                             "body": f"Hi {profile['name']},\n\nAre you currently open to discussing opportunities in {area}? What criteria and timing should I understand before suggesting a next step?\n\nIf you prefer no further contact, please let me know.",
                             "sending_enabled": False}
                saved_drafts = []
                for row in connection.execute("SELECT * FROM relationship_drafts WHERE relationship_id=? ORDER BY rowid DESC", (rid,)):
                    saved = self.decode(row)
                    saved["reviews"] = [self.decode(review) for review in connection.execute("SELECT * FROM relationship_draft_reviews WHERE draft_id=? ORDER BY rowid DESC", (saved["id"],))]
                    saved["review_blockers"] = self.draft_blockers(connection, rid, profile, saved)
                    saved["review_status"] = "blocked" if saved["review_blockers"] else saved["reviews"][0]["decision"] if saved["reviews"] else "pending"
                    saved["recipient"] = self.decode(connection.execute("SELECT * FROM relationship_profiles WHERE id=?", (saved["profile_id"],)).fetchone())["email"]
                    saved["sending_enabled"] = False
                    saved_drafts.append(saved)
                records.append({"id": rid, "profile": profile, "profile_history": history,
                    "interactions": interactions, "event_id": last["id"] if last else None,
                    "buyer": dict(buyer) if buyer else None, "blocked": blocked, "paused": paused,
                    "follow_up_on": due, "next_action": schedule["next_action"],
                    "due": bool(eligible and due and due <= today), "overdue": bool(eligible and due and due < today),
                    "queue_status": "blocked" if blocked else "paused" if paused else "unscheduled" if not due else "due" if due <= today else "upcoming",
                    "draft": draft, "saved_drafts": saved_drafts})
            records.sort(key=lambda r: (r["blocked"] or r["paused"], not r["due"], not bool(r["follow_up_on"]), r["follow_up_on"], r["profile"]["name"].casefold(), r["id"]))
            buyers = [dict(row) for row in connection.execute("SELECT id,name,status FROM buyers ORDER BY name,id")]
        return {"today": today, "sending_enabled": False, "relationships": records, "buyers": buyers,
                "summary": {"total": len(records), "due": sum(r["due"] for r in records),
                    "overdue": sum(r["overdue"] for r in records), "blocked": sum(r["blocked"] for r in records)},
                "daily_focus": [r["id"] for r in records if r["due"]][:10]}
