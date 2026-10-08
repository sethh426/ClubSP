"""Property-independent relationships and owner-entered follow-ups; no transport."""
from datetime import date, datetime, timedelta, timezone
import json
import re
from uuid import UUID, uuid4

from core.memory.models import utc_now
from .operations import business_today
from .validation import list_field, number_field, text_field
from .schema import assert_component_compatible, ensure_component


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


def _migrate_relationships_v1_to_v2(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS relationship_buyer_qualifications (
            id TEXT PRIMARY KEY,
            request_key TEXT NOT NULL UNIQUE,
            relationship_id TEXT NOT NULL REFERENCES relationships(id),
            source_profile_id TEXT NOT NULL REFERENCES relationship_profiles(id),
            source_event_id TEXT NOT NULL REFERENCES relationship_interactions(id),
            buyer_id TEXT NOT NULL REFERENCES buyers(id),
            mandate_id TEXT NOT NULL REFERENCES buyer_mandates(id),
            linked_profile_id TEXT NOT NULL REFERENCES relationship_profiles(id),
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS relationship_buyer_qualification_lookup "
        "ON relationship_buyer_qualifications(relationship_id,created_at)"
    )


class RelationshipBook:
    @staticmethod
    def pause_confirmed_demand(connection, rid):
        """Withdraw this relationship's confirmed requests; preserve their evidence."""
        rows = connection.execute("SELECT buyer_id,mandate_id FROM relationship_buyer_qualifications WHERE relationship_id=?", (rid,)).fetchall()
        for row in rows:
            connection.execute("UPDATE buyer_mandates SET status='paused' WHERE id=? AND status='active'", (row["mandate_id"],))
        for buyer_id in {row["buyer_id"] for row in rows}:
            if not connection.execute("SELECT 1 FROM buyer_mandates WHERE buyer_id=? AND status='active'", (buyer_id,)).fetchone():
                connection.execute("UPDATE buyers SET status='paused' WHERE id=?", (buyer_id,))

    def __init__(self, application):
        self.application = application
        self.database = application.database
        with self.database.session(write=True) as (connection, _):
            relationship_schema = assert_component_compatible(connection, "relationships")
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
                "CREATE TABLE IF NOT EXISTS relationship_sends (id TEXT PRIMARY KEY, relationship_id TEXT NOT NULL REFERENCES relationships(id), draft_id TEXT NOT NULL UNIQUE REFERENCES relationship_drafts(id), review_id TEXT NOT NULL REFERENCES relationship_draft_reviews(id), request_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS relationship_send_lookup ON relationship_sends(relationship_id,created_at)",
                "CREATE TABLE IF NOT EXISTS relationship_send_events (id TEXT PRIMARY KEY, send_id TEXT NOT NULL REFERENCES relationship_sends(id), status TEXT NOT NULL, provider_message_id TEXT NOT NULL, provider_thread_id TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS relationship_send_event_lookup ON relationship_send_events(send_id,created_at)",
            ]
            for statement in statements:
                connection.execute(statement)
            if relationship_schema == 0:
                ensure_component(connection, "relationships", target=1)
                relationship_schema = 1
            if relationship_schema < 2:
                ensure_component(
                    connection, "relationships",
                    migrations={1: _migrate_relationships_v1_to_v2},
                )

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
            if payload["status"] in {"paused", "closed"} or payload["permission"] == "blocked":
                self.pause_confirmed_demand(connection, rid)
            return self.decode(connection.execute("SELECT * FROM relationship_profiles WHERE id=?", (pid,)).fetchone())

    def review_permission(self, rid, data):
        key = identifier(data, "request_key")
        profile_id = identifier(data, "profile_id")
        event_id = identifier(data, "event_id", False)
        decision = text_field(data, "decision", 20)
        evidence_reference = text_field(data, "evidence_reference", 500)
        review_note = text_field(data, "review_note", 2000)
        if data.get("owner_confirmed_review") is not True:
            raise ValueError("Explicit owner confirmation of the contact-permission review is required")
        if decision not in {"allow_outreach", "block_outreach"}:
            raise ValueError("Choose allow outreach or do not contact")
        with self.database.session(write=True) as (connection, _):
            existing = connection.execute(
                "SELECT * FROM relationship_profiles WHERE request_key=?", (key,)
            ).fetchone()
            next_permission = "owner_reviewed" if decision == "allow_outreach" else "blocked"
            if existing:
                saved = self.decode(existing)
                audit = connection.execute(
                    "SELECT payload FROM relationship_interactions WHERE relationship_id=? AND request_key=?",
                    (rid, "permission:" + key),
                ).fetchone()
                saved_note = json.loads(audit["payload"])["note"] if audit else ""
                if (existing["relationship_id"] != rid or existing["previous_id"] != profile_id
                        or saved["permission"] != next_permission
                        or saved["permission_reference"] != evidence_reference
                        or saved_note != "Contact permission review: " + review_note):
                    raise ValueError("request_key already records a different permission review")
                saved["review_note"] = review_note
                return saved
            current_row = self.context(connection, rid, profile_id, event_id)
            current = self.decode(current_row)
            if current["permission"] == "blocked" and next_permission == "owner_reviewed":
                raise ValueError("Recorded suppression cannot be cleared in this release")
            if connection.execute(
                "SELECT 1 FROM relationship_stops WHERE relationship_id=?", (rid,)
            ).fetchone() and next_permission == "owner_reviewed":
                raise ValueError("Recorded suppression cannot be cleared in this release")
            if current["email"] and email_blocked(connection, current["email"]) and next_permission == "owner_reviewed":
                raise ValueError("Recorded suppression cannot be cleared in this release")

            payload = {
                name: current[name] for name in [
                    "name", "company", "email", "kind", "status", "needs",
                    "source_reference", "owner", "markets", "buyer_id", "follow_up_on",
                ]
            }
            payload["permission"] = next_permission
            payload["permission_reference"] = evidence_reference
            payload["event_id_at_review"] = event_id
            if next_permission == "owner_reviewed":
                if current["kind"] == "investor" and not current.get("buyer_id"):
                    payload["next_action"] = "Confirm the investor's current buy box, funding evidence and closing capacity"
                else:
                    payload["next_action"] = current.get("next_action") or "Review the relationship and record the next action"
            else:
                payload["status"] = "paused" if current["status"] not in {"closed"} else current["status"]
                payload["follow_up_on"] = ""
                payload["next_action"] = ""

            encoded = json.dumps(payload, sort_keys=True)

            pid = str(uuid4())
            connection.execute(
                "INSERT INTO relationship_profiles VALUES(?,?,?,?,?,?)",
                (pid, rid, profile_id, key, encoded, utc_now().isoformat()),
            )
            interaction_id = str(uuid4())
            interaction_payload = {
                "direction": "note",
                "outcome": "general" if next_permission == "owner_reviewed" else "stop",
                "note": "Contact permission review: " + review_note,
                "evidence_reference": evidence_reference,
                "occurred_on": business_today().isoformat(),
                "follow_up_on": payload["follow_up_on"],
                "next_action": payload["next_action"],
            }
            connection.execute(
                "INSERT INTO relationship_interactions VALUES(?,?,?,?,?,?,?)",
                (
                    interaction_id, rid, "permission:" + key, pid, event_id,
                    json.dumps(interaction_payload, sort_keys=True), utc_now().isoformat(),
                ),
            )
            if next_permission == "blocked":
                self.pause_confirmed_demand(connection, rid)
            result = self.decode(connection.execute(
                "SELECT * FROM relationship_profiles WHERE id=?", (pid,)
            ).fetchone())
            result["review_note"] = review_note
            return result

    def interact(self, rid, data):
        key = identifier(data, "request_key")
        profile_id = identifier(data, "profile_id")
        event_id = identifier(data, "event_id", False)
        payload = {name: text_field(data, name, limit) for name, limit in [
            ("direction", 20), ("outcome", 30), ("note", 4000), ("evidence_reference", 500)]}
        if payload["direction"] not in {"incoming", "outgoing", "note"} or payload["outcome"] not in {"general", "interested", "not_interested", "stop", "wrong_person", "buying_paused"}:
            raise ValueError("Unsupported interaction direction or outcome")
        if payload["outcome"] == "buying_paused" and payload["direction"] != "incoming":
            raise ValueError("A buying pause requires an actual incoming buyer response")
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
            if stop or payload["outcome"] == "buying_paused":
                self.pause_confirmed_demand(connection, rid)
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

    def _send_json(self, connection, row):
        item = self.decode(row)
        event = connection.execute(
            "SELECT * FROM relationship_send_events WHERE send_id=? ORDER BY rowid DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        item["latest_event"] = dict(event) if event else None
        item["status"] = event["status"] if event else "reserved"
        return item

    def qualify_buyer(self, rid, data):
        key = identifier(data, "request_key")
        profile_id = identifier(data, "profile_id")
        event_id = identifier(data, "event_id")
        if data.get("owner_confirmed_criteria") is not True:
            raise ValueError("Explicit owner confirmation of the recorded buyer criteria is required")

        markets = list_field(data, "markets", max_items=50)
        strategies = list_field(data, "strategies", allowed={"assignment", "resale"}, max_items=2)
        property_types = sorted({
            value.lower().replace(" ", "_")
            for value in list_field(data, "property_types", max_items=30, required=False)
        })
        max_total_price = number_field(data, "max_total_price", nonnegative=True)
        max_repairs = number_field(data, "max_repairs", nonnegative=True)
        if max_total_price <= 0:
            raise ValueError("max_total_price must be greater than zero")

        funding_status = text_field(data, "funding_status", 30)
        if funding_status not in {"unverified", "owner_reviewed", "verified"}:
            raise ValueError("Unsupported funding status")
        funding_reference = text_field(
            data, "funding_reference", 500,
            required=funding_status in {"owner_reviewed", "verified"},
        )
        review_note = text_field(data, "review_note", 2000)

        refresh_value = number_field(data, "refresh_days", default=30, nonnegative=True)
        priority_value = number_field(data, "priority", default=50, nonnegative=True)
        max_active_value = number_field(data, "max_active_reservations", default=1, nonnegative=True)
        target_units_value = number_field(data, "target_units_per_month", default=1, nonnegative=True)
        for label, value in {
            "refresh_days": refresh_value, "priority": priority_value,
            "max_active_reservations": max_active_value,
            "target_units_per_month": target_units_value,
        }.items():
            if not value.is_integer():
                raise ValueError(f"{label} must be a whole number")
        refresh_days, priority = int(refresh_value), int(priority_value)
        max_active_reservations, target_units_per_month = int(max_active_value), int(target_units_value)
        if not 1 <= refresh_days <= 90:
            raise ValueError("refresh_days must be between 1 and 90")
        if not 0 <= priority <= 100:
            raise ValueError("priority must be between 0 and 100")
        if not 1 <= max_active_reservations <= 100:
            raise ValueError("max_active_reservations must be between 1 and 100")
        if not 1 <= target_units_per_month <= 1000:
            raise ValueError("target_units_per_month must be between 1 and 1000")

        mandate_name = text_field(data, "mandate_name", 160, required=False)
        user_payload = {
            "markets": markets, "strategies": strategies, "property_types": property_types,
            "max_total_price": max_total_price, "max_repairs": max_repairs,
            "funding_status": funding_status, "funding_reference": funding_reference,
            "review_note": review_note, "refresh_days": refresh_days, "priority": priority,
            "max_active_reservations": max_active_reservations,
            "target_units_per_month": target_units_per_month,
            "mandate_name": mandate_name,
            "owner_confirmed_criteria": True,
        }
        encoded = json.dumps(user_payload, sort_keys=True, allow_nan=False)

        with self.database.session(write=True) as (connection, _):
            existing = connection.execute(
                "SELECT * FROM relationship_buyer_qualifications WHERE request_key=?", (key,)
            ).fetchone()
            if existing:
                if (existing["relationship_id"], existing["source_profile_id"],
                        existing["source_event_id"], existing["payload"]) != (
                        rid, profile_id, event_id, encoded):
                    raise ValueError("request_key already records different buyer qualification data")
                return {
                    "id": existing["id"], "relationship_id": rid,
                    "buyer_id": existing["buyer_id"], "mandate_id": existing["mandate_id"],
                    "linked_profile_id": existing["linked_profile_id"], **user_payload,
                }

            profile_row = self.latest(connection, "relationship_profiles", rid)
            event_row = self.latest(connection, "relationship_interactions", rid)
            if not profile_row:
                raise LookupError("Relationship not found")
            if profile_row["id"] != profile_id or not event_row or event_row["id"] != event_id:
                raise ValueError("Relationship or conversation changed; refresh before qualifying buyer demand")

            profile = self.decode(profile_row)
            event = self.decode(event_row)
            if profile["kind"] != "investor":
                raise ValueError("Only investor relationships can be qualified as buyer demand")
            if profile["status"] in {"paused", "closed"}:
                raise ValueError("Paused or closed relationships cannot be qualified")
            prior_qualification = connection.execute("SELECT * FROM relationship_buyer_qualifications WHERE relationship_id=? ORDER BY rowid DESC LIMIT 1", (rid,)).fetchone()
            renewing = bool(profile.get("buyer_id"))
            if renewing and (not prior_qualification or prior_qualification["buyer_id"] != profile["buyer_id"] or prior_qualification["source_event_id"] == event_id):
                raise ValueError("This relationship is already linked to a buyer; renewal requires a new incoming criteria reply")
            if profile["permission"] == "blocked" or email_blocked(connection, profile["email"]) or connection.execute(
                    "SELECT 1 FROM relationship_stops WHERE relationship_id=?", (rid,)).fetchone():
                raise ValueError("Do-not-contact relationship cannot be promoted to active buyer demand")
            if event["direction"] != "incoming" or event["outcome"] not in {"general", "interested"}:
                raise ValueError("Qualifying buyer demand requires the latest incoming criteria conversation")
            if not event["evidence_reference"]:
                raise ValueError("Incoming criteria conversation requires evidence")

            # The confirmation window starts with the actual conversation, not
            # the day an owner finally enters or qualifies it. Preserve only
            # the remaining window; expired evidence requires a new reply.
            criteria_age = (business_today() - date.fromisoformat(event["occurred_on"])).days
            if criteria_age < 0 or criteria_age >= refresh_days:
                raise ValueError("Incoming buying criteria have expired; reconfirm with the buyer before qualifying demand")
            now_dt = utc_now()
            now = now_dt.isoformat()
            expires_at = (now_dt + timedelta(days=refresh_days - criteria_age)).isoformat()
            buyer_id, mandate_id, linked_profile_id, qualification_id = (
                profile["buyer_id"] if renewing else str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())
            )
            buyer_locations = sorted({" ".join(value.strip().lower().split()) for value in markets})
            buyer_verified_at = now if funding_status == "verified" else ""

            if renewing:
                # Only fresh, explicitly reviewed criteria can replace the old request.
                self.pause_confirmed_demand(connection, rid)
                connection.execute(
                    "UPDATE buyers SET locations_json=?,strategies_json=?,property_types_json=?,max_total_price=?,max_repairs=?,funding_status=?,verified_at=?,verification_reference=?,status='active' WHERE id=?",
                    (json.dumps(buyer_locations), json.dumps(strategies), json.dumps(property_types), max_total_price, max_repairs, funding_status, buyer_verified_at, funding_reference, buyer_id),
                )
            else:
                connection.execute(
                    "INSERT INTO buyers(id,name,company,locations_json,strategies_json,property_types_json,"
                    "max_total_price,max_repairs,funding_status,verified_at,verification_reference,status,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        buyer_id, profile["name"], profile["company"], json.dumps(buyer_locations),
                        json.dumps(strategies), json.dumps(property_types), max_total_price, max_repairs,
                        funding_status, buyer_verified_at, funding_reference, "active", now,
                    ),
                )
            connection.execute(
                """INSERT INTO buyer_mandates(
                    id,buyer_id,name,markets_json,strategies_json,property_types_json,
                    max_total_price,max_repairs,priority,status,evidence_reference,
                    verified_at,expires_at,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    mandate_id, buyer_id,
                    mandate_name or ("Current criteria — " + (profile["company"] or profile["name"])),
                    json.dumps(markets), json.dumps(strategies), json.dumps(property_types),
                    max_total_price, max_repairs, priority, "active",
                    "relationship_interaction:" + event_id, now, expires_at, now,
                ),
            )
            connection.execute(
                "INSERT INTO buyer_mandate_filters(mandate_id,filters_json) VALUES(?,?)",
                (mandate_id, "{}"),
            )
            connection.execute(
                "INSERT INTO buyer_mandate_capacity(mandate_id,max_active_reservations,target_units_per_month) "
                "VALUES(?,?,?)",
                (mandate_id, max_active_reservations, target_units_per_month),
            )

            profile_payload = {
                name: profile[name] for name in [
                    "name", "company", "email", "kind", "needs", "source_reference",
                    "permission", "permission_reference", "owner", "markets",
                ]
            }
            profile_payload.update({
                "status": "active", "buyer_id": buyer_id,
                "follow_up_on": (business_today() + timedelta(days=refresh_days - criteria_age)).isoformat(),
                "next_action": "Reconfirm buyer criteria before the current mandate expires",
                "event_id_at_review": event_id,
            })
            profile_request_key = str(uuid4())
            connection.execute(
                "INSERT INTO relationship_profiles VALUES(?,?,?,?,?,?)",
                (
                    linked_profile_id, rid, profile_id, profile_request_key,
                    json.dumps(profile_payload, sort_keys=True), now,
                ),
            )
            connection.execute(
                "INSERT INTO relationship_buyer_qualifications VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    qualification_id, key, rid, profile_id, event_id, buyer_id, mandate_id,
                    linked_profile_id, encoded, now,
                ),
            )

        return {
            "id": qualification_id, "relationship_id": rid, "buyer_id": buyer_id,
            "mandate_id": mandate_id, "linked_profile_id": linked_profile_id,
            "expires_at": expires_at, **user_payload,
        }

    def reserve_send(self, rid, data, sender_email):
        key = identifier(data, "request_key")
        draft_id = identifier(data, "draft_id")
        review_id = identifier(data, "review_id")
        if data.get("owner_confirmed_send") is not True:
            raise ValueError("Explicit owner send confirmation is required")
        follow_up_on = day(data, "follow_up_on")
        next_action = text_field(data, "next_action", 500, required=bool(follow_up_on))
        sender_email = str(sender_email or "").strip().lower()
        if not sender_email:
            raise ValueError("Configured Gmail sender is missing")
        with self.database.session(write=True) as (connection, _):
            existing = connection.execute(
                "SELECT * FROM relationship_sends WHERE request_key=?", (key,)
            ).fetchone()
            if existing:
                saved = self.decode(existing)
                expected = {
                    "owner_confirmed_send": True,
                    "follow_up_on": follow_up_on,
                    "next_action": next_action,
                    "sender": sender_email,
                }
                if (existing["relationship_id"] != rid or existing["draft_id"] != draft_id
                        or existing["review_id"] != review_id
                        or any(saved.get(k) != v for k, v in expected.items())):
                    raise ValueError("request_key already records a different send attempt")
                return {"created": False, "send": self._send_json(connection, existing)}

            prior = connection.execute(
                "SELECT * FROM relationship_sends WHERE draft_id=?", (draft_id,)
            ).fetchone()
            if prior:
                raise ValueError("This exact draft already has a send attempt; reconcile it before any new outreach")

            draft = connection.execute(
                "SELECT * FROM relationship_drafts WHERE id=? AND relationship_id=?",
                (draft_id, rid),
            ).fetchone()
            if not draft:
                raise LookupError("Draft not found for this relationship")
            profile = self.decode(self.latest(connection, "relationship_profiles", rid))
            blockers = self.draft_blockers(connection, rid, profile, draft)
            if blockers:
                raise ValueError("; ".join(blockers))
            review = connection.execute(
                "SELECT * FROM relationship_draft_reviews WHERE id=? AND draft_id=?",
                (review_id, draft_id),
            ).fetchone()
            latest_review = connection.execute(
                "SELECT * FROM relationship_draft_reviews WHERE draft_id=? ORDER BY rowid DESC LIMIT 1",
                (draft_id,),
            ).fetchone()
            if not review or not latest_review or latest_review["id"] != review_id:
                raise ValueError("Use the current review for this draft")
            reviewed = self.decode(review)
            if reviewed["decision"] != "approved":
                raise ValueError("The current draft review is not approved")
            message = self.decode(draft)
            payload = {
                "recipient": profile["email"],
                "sender": sender_email,
                "subject": message["subject"],
                "body": message["body"],
                "profile_id": profile["id"],
                "event_id": (self.latest(connection, "relationship_interactions", rid) or {"id": None})["id"],
                "owner_confirmed_send": True,
                "follow_up_on": follow_up_on,
                "next_action": next_action,
            }
            send_id = str(uuid4())
            now = utc_now().isoformat()
            connection.execute(
                "INSERT INTO relationship_sends VALUES(?,?,?,?,?,?,?)",
                (send_id, rid, draft_id, review_id, key, json.dumps(payload, sort_keys=True), now),
            )
            connection.execute(
                "INSERT INTO relationship_send_events VALUES(?,?,?,?,?,?,?)",
                (str(uuid4()), send_id, "reserved", "", "", "Reserved before Gmail transport", now),
            )
            row = connection.execute("SELECT * FROM relationship_sends WHERE id=?", (send_id,)).fetchone()
            return {"created": True, "send": self._send_json(connection, row)}

    def record_send_result(self, send_id, status, provider_message_id="", provider_thread_id="", note=""):
        if status not in {"sent", "unknown"}:
            raise ValueError("Unsupported send result")
        send_id = identifier({"send_id": send_id}, "send_id")
        if status == "sent" and (not provider_message_id or not provider_thread_id):
            raise ValueError("Sent Gmail records require message and thread IDs")
        with self.database.session(write=True) as (connection, _):
            row = connection.execute("SELECT * FROM relationship_sends WHERE id=?", (send_id,)).fetchone()
            if not row:
                raise LookupError("Send record not found")
            latest = connection.execute(
                "SELECT * FROM relationship_send_events WHERE send_id=? ORDER BY rowid DESC LIMIT 1",
                (send_id,),
            ).fetchone()
            if latest and latest["status"] in {"sent", "unknown"}:
                if (latest["status"], latest["provider_message_id"], latest["provider_thread_id"]) != (
                        status, provider_message_id, provider_thread_id):
                    raise ValueError("Send result is already finalized")
                return self._send_json(connection, row)
            now = utc_now().isoformat()
            connection.execute(
                "INSERT INTO relationship_send_events VALUES(?,?,?,?,?,?,?)",
                (str(uuid4()), send_id, status, provider_message_id, provider_thread_id,
                 text_field({"note": note}, "note", 1000, required=False), now),
            )
            if status == "sent":
                payload = json.loads(row["payload"])
                current_profile = self.latest(connection, "relationship_profiles", row["relationship_id"])
                current_event = self.latest(connection, "relationship_interactions", row["relationship_id"])
                context_unchanged = bool(
                    current_profile and current_profile["id"] == payload["profile_id"]
                    and (current_event["id"] if current_event else None) == payload["event_id"]
                )
                already_recorded = connection.execute(
                    "SELECT 1 FROM relationship_interactions WHERE request_key=?", (send_id,)
                ).fetchone()
                if context_unchanged and not already_recorded:
                    interaction = {
                        "direction": "outgoing",
                        "outcome": "general",
                        "note": "Sent approved Gmail draft: " + payload["subject"],
                        "evidence_reference": "gmail_message:" + provider_message_id,
                        "occurred_on": business_today().isoformat(),
                        "follow_up_on": payload["follow_up_on"],
                        "next_action": payload["next_action"],
                    }
                    connection.execute(
                        "INSERT INTO relationship_interactions VALUES(?,?,?,?,?,?,?)",
                        (str(uuid4()), row["relationship_id"], send_id, payload["profile_id"],
                         payload["event_id"], json.dumps(interaction, sort_keys=True), now),
                    )
            return self._send_json(connection, row)

    def review_gmail_reply(self, preview_id, data):
        preview_id = identifier({"preview_id": preview_id}, "preview_id")
        action = text_field(data, "action", 20)
        if action not in {"link", "unlink", "import"}:
            raise ValueError("Unsupported Gmail relationship review action")
        relationship_id = identifier(data, "relationship_id", required=action == "link")
        if action == "unlink":
            with self.database.session(write=True) as (connection, _):
                link = connection.execute(
                    "SELECT * FROM gmail_preview_relationship_links WHERE preview_id=?", (preview_id,)
                ).fetchone()
                if not link:
                    return {"action": "unlink", "id": preview_id}
                if link["interaction_id"]:
                    raise ValueError("Imported replies cannot be unlinked; correct the relationship history instead")
                connection.execute("DELETE FROM gmail_preview_relationship_links WHERE preview_id=?", (preview_id,))
            return {"action": "unlink", "id": preview_id}

        if action == "link":
            with self.database.session(write=True) as (connection, _):
                preview = connection.execute("SELECT * FROM gmail_previews WHERE id=?", (preview_id,)).fetchone()
                if not preview:
                    raise LookupError("Gmail preview not found")
                profile = self.latest(connection, "relationship_profiles", relationship_id)
                if not profile:
                    raise LookupError("Relationship not found")
                current = self.decode(profile)
                if not preview["sender_email"] or current["email"].lower() != preview["sender_email"].lower():
                    raise ValueError("Choose a relationship whose current email matches the Gmail sender")
                existing = connection.execute(
                    "SELECT * FROM gmail_preview_relationship_links WHERE preview_id=?", (preview_id,)
                ).fetchone()
                if existing and existing["interaction_id"]:
                    raise ValueError("Imported replies cannot be relinked")
                if existing and existing["relationship_id"] == relationship_id:
                    return {"action": "link", "id": preview_id, "relationship_id": relationship_id}
                now = utc_now().isoformat()
                connection.execute(
                    "INSERT OR REPLACE INTO gmail_preview_relationship_links(preview_id,relationship_id,interaction_id,linked_at,imported_at) VALUES(?,?,?,?,?)",
                    (preview_id, relationship_id, None, now, None),
                )
            return {"action": "link", "id": preview_id, "relationship_id": relationship_id}

        key = identifier(data, "request_key")
        outcome = text_field(data, "outcome", 30)
        if outcome not in {"general", "interested", "not_interested", "stop", "wrong_person"}:
            raise ValueError("Unsupported reply outcome")
        occurred_on = day(data, "occurred_on", required=True, past=True)
        follow_up_on = day(data, "follow_up_on")
        next_action = text_field(data, "next_action", 500, required=bool(follow_up_on))
        review_note = text_field(data, "review_note", 2000)
        if outcome in {"stop", "wrong_person"}:
            follow_up_on, next_action = "", ""

        with self.database.session() as (connection, _):
            preview = connection.execute("SELECT * FROM gmail_previews WHERE id=?", (preview_id,)).fetchone()
            if not preview:
                raise LookupError("Gmail preview not found")
            link = connection.execute(
                "SELECT * FROM gmail_preview_relationship_links WHERE preview_id=?", (preview_id,)
            ).fetchone()
            if not link:
                raise ValueError("Link this Gmail preview to a matching relationship before importing it")
            if relationship_id and relationship_id != link["relationship_id"]:
                raise ValueError("Gmail preview relationship link changed; reload before importing")
            relationship_id = link["relationship_id"]
            if link["interaction_id"]:
                prior = connection.execute(
                    "SELECT * FROM relationship_interactions WHERE id=?", (link["interaction_id"],)
                ).fetchone()
                if prior and prior["request_key"] == key:
                    return {"action": "import", "id": preview_id, "relationship_id": relationship_id,
                            "interaction": self.decode(prior)}
                raise ValueError("This Gmail preview is already imported")
            profile = self.latest(connection, "relationship_profiles", relationship_id)
            if not profile:
                raise LookupError("Relationship not found")
            profile_data = self.decode(profile)
            if not preview["sender_email"] or profile_data["email"].lower() != preview["sender_email"].lower():
                raise ValueError("Relationship email changed; relink the preview before importing")
            latest_event = self.latest(connection, "relationship_interactions", relationship_id)
            profile_id = profile["id"]
            event_id = latest_event["id"] if latest_event else ""
            evidence = "gmail_preview:" + preview_id + ";gmail_message:" + preview["gmail_id"]
            note = "Gmail preview: " + (preview["snippet"] or "(no snippet)") + "\nOwner review: " + review_note

        interaction = self.interact(relationship_id, {
            "request_key": key, "profile_id": profile_id, "event_id": event_id,
            "direction": "incoming", "outcome": outcome, "note": note,
            "evidence_reference": evidence, "occurred_on": occurred_on,
            "follow_up_on": follow_up_on, "next_action": next_action,
        })
        with self.database.session(write=True) as (connection, _):
            link = connection.execute(
                "SELECT * FROM gmail_preview_relationship_links WHERE preview_id=?", (preview_id,)
            ).fetchone()
            if not link or link["relationship_id"] != relationship_id:
                raise ValueError("Gmail relationship link changed during import")
            if link["interaction_id"] and link["interaction_id"] != interaction["id"]:
                raise ValueError("Gmail preview was imported concurrently")
            connection.execute(
                "UPDATE gmail_preview_relationship_links SET interaction_id=?,imported_at=? WHERE preview_id=?",
                (interaction["id"], utc_now().isoformat(), preview_id),
            )
        return {"action": "import", "id": preview_id, "relationship_id": relationship_id,
                "interaction": interaction}

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
                qualification_row = connection.execute(
                    "SELECT * FROM relationship_buyer_qualifications WHERE relationship_id=? ORDER BY rowid DESC LIMIT 1",
                    (rid,),
                ).fetchone()
                qualification = self.decode(qualification_row) if qualification_row else None
                if qualification:
                    mandate = connection.execute(
                        "SELECT * FROM buyer_mandates WHERE id=?", (qualification["mandate_id"],)
                    ).fetchone()
                    qualification["mandate"] = dict(mandate) if mandate else None
                    if qualification["mandate"]:
                        for key in ("markets_json", "strategies_json", "property_types_json"):
                            qualification["mandate"][key[:-5]] = json.loads(qualification["mandate"].pop(key))
                    m = qualification["mandate"]
                    qualification["demand_status"] = (
                        "blocked" if blocked else "paused" if paused or not m or m["status"] != "active" or not buyer or buyer["status"] != "active" else
                        "expired" if m["expires_at"] and datetime.fromisoformat(m["expires_at"]) <= utc_now() else "current"
                    )
                draft = None
                if eligible and profile["email"] and profile["permission"] == "owner_reviewed":
                    area = ", ".join(profile["markets"]) or "your preferred areas"
                    if profile["kind"] == "investor":
                        draft = {
                            "subject": "Your current buying priorities",
                            "body": (
                                f"Hi {profile['name']},\n\n"
                                f"I’m mapping active buyers in {area} before I send opportunities around. "
                                "Are you actively buying there? If so, could you share your current buy box: "
                                "areas or neighborhoods, property types, whether you accept assignment deals, "
                                "maximum purchase price, repair tolerance, how you typically fund purchases, "
                                "and your normal closing window?\n\n"
                                "I’ll use that to avoid sending you deals that do not fit. "
                                "If you prefer no further contact, please let me know."
                            ),
                            "sending_enabled": False,
                        }
                    else:
                        draft = {
                            "subject": "A possible working relationship",
                            "body": (
                                f"Hi {profile['name']},\n\n"
                                f"Are you currently open to discussing opportunities in {area}? "
                                "What criteria and timing should I understand before suggesting a next step?\n\n"
                                "If you prefer no further contact, please let me know."
                            ),
                            "sending_enabled": False,
                        }
                saved_drafts = []
                for row in connection.execute("SELECT * FROM relationship_drafts WHERE relationship_id=? ORDER BY rowid DESC", (rid,)):
                    saved = self.decode(row)
                    saved["reviews"] = [self.decode(review) for review in connection.execute("SELECT * FROM relationship_draft_reviews WHERE draft_id=? ORDER BY rowid DESC", (saved["id"],))]
                    saved["review_blockers"] = self.draft_blockers(connection, rid, profile, saved)
                    saved["review_status"] = "blocked" if saved["review_blockers"] else saved["reviews"][0]["decision"] if saved["reviews"] else "pending"
                    saved["recipient"] = self.decode(connection.execute("SELECT * FROM relationship_profiles WHERE id=?", (saved["profile_id"],)).fetchone())["email"]
                    send_row = connection.execute(
                        "SELECT * FROM relationship_sends WHERE draft_id=? ORDER BY rowid DESC LIMIT 1",
                        (saved["id"],),
                    ).fetchone()
                    saved["send"] = self._send_json(connection, send_row) if send_row else None
                    current_review = saved["reviews"][0] if saved["reviews"] else None
                    saved["sending_enabled"] = bool(
                        not saved["review_blockers"] and current_review
                        and current_review["decision"] == "approved" and not send_row
                    )
                    saved_drafts.append(saved)
                records.append({"id": rid, "profile": profile, "profile_history": history,
                    "interactions": interactions, "event_id": last["id"] if last else None,
                    "buyer": dict(buyer) if buyer else None, "qualification": qualification,
                    "blocked": blocked, "paused": paused,
                    "follow_up_on": due, "next_action": schedule["next_action"],
                    "due": bool(eligible and due and due <= today), "overdue": bool(eligible and due and due < today),
                    "queue_status": "blocked" if blocked else "paused" if paused else "unscheduled" if not due else "due" if due <= today else "upcoming",
                    "draft": draft, "saved_drafts": saved_drafts})
            records.sort(key=lambda r: (r["blocked"] or r["paused"], not r["due"], not bool(r["follow_up_on"]), r["follow_up_on"], r["profile"]["name"].casefold(), r["id"]))
            buyers = [dict(row) for row in connection.execute("SELECT id,name,status FROM buyers ORDER BY name,id")]
        return {"today": today, "sending_enabled": False, "relationships": records, "buyers": buyers,
                "summary": {"total": len(records), "due": sum(r["due"] for r in records),
                    "overdue": sum(r["overdue"] for r in records), "blocked": sum(r["blocked"] for r in records),
                    "qualified_buyers": sum(bool(r["qualification"] and r["qualification"].get("demand_status") == "current") for r in records)},
                "daily_focus": [r["id"] for r in records if r["due"]][:10]}
