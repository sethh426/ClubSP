"""Private draft packets for a proposed property representative; no transport."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import UUID, uuid4

from .schema import assert_component_compatible, ensure_component

TASKS = ["Discuss representation scope, fees, conflicts and client authority before acting.",
         "Confirm listing availability, asking price, property identity and rental feasibility.",
         "Arrange condition, repair-cost, lease and restriction checks with appropriate professionals.",
         "Coordinate title, financing and closing diligence with the client and relevant professionals.",
         "Negotiate or submit offers only under an agreed mandate and separately approved terms."]
BOUNDARY = ("Draft research handoff only. The named company has not accepted this request. "
            "Representation, license status, fees, funding and authority are unverified. "
            "This packet is not a representation agreement, offer, purchase commitment or instruction to transact. "
            "ClubSP supplies research and workflow; a separately engaged representative handles agreed transaction work.")


def _text(data, key, maximum, required=True):
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 and (key != "notes" or c not in "\n\t") for c in value):
        raise ValueError(f"Invalid {key}")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{key} is required")
    return value


def _encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


class RepresentativeHandoffMixin:
    def _initialize_representative_handoffs(self):
        with self.database.session(write=True) as (connection, _):
            assert_component_compatible(connection, "representative_handoffs")
            connection.execute("""CREATE TABLE IF NOT EXISTS representative_handoffs (
                id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
                brief_id TEXT NOT NULL REFERENCES acquisition_briefs(id), snapshot_json TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL, created_at TEXT NOT NULL)""")
            ensure_component(connection, "representative_handoffs")

    @staticmethod
    def _handoff_record(row):
        snapshot = json.loads(row["snapshot_json"])
        if sha256(row["snapshot_json"].encode()).hexdigest() != row["snapshot_sha256"]:
            raise ValueError("Saved handoff integrity check failed")
        return {"id": row["id"], "brief_id": row["brief_id"], "created_at": row["created_at"],
                "status": "draft_not_sent", "snapshot": snapshot, "snapshot_sha256": row["snapshot_sha256"],
                "representation_established": False, "external_actions": False}

    def representative_handoff_history(self):
        with self.database.session() as (connection, _):
            return {"handoffs": [self._handoff_record(row) for row in connection.execute(
                "SELECT * FROM representative_handoffs ORDER BY created_at DESC,id DESC LIMIT 20")]}

    def prepare_representative_handoff(self, data, *, automation_revision=None):
        fields = {"request_key", "brief_id", "card_indices", "client_name", "representative_company", "representative_contact", "notes"}
        if not isinstance(data, dict) or set(data) - fields:
            raise ValueError("Unsupported handoff fields")
        body = {key: _text(data, key, limit, required) for key, limit, required in
                [("request_key", 100, True), ("brief_id", 36, True), ("client_name", 200, True),
                 ("representative_company", 200, True), ("representative_contact", 200, False), ("notes", 2000, False)]}
        try:
            UUID(body["brief_id"])
        except ValueError:
            raise ValueError("Invalid brief identity") from None
        indices = data.get("card_indices")
        if (not isinstance(indices, list) or not 1 <= len(indices) <= 3 or
                any(type(i) is not int or i < 0 or i > 2 for i in indices) or len(set(indices)) != len(indices)):
            raise ValueError("Select 1–3 distinct property cards")
        body["card_indices"] = sorted(indices)
        request_hash = sha256(_encoded(body).encode()).hexdigest()
        now = datetime.now(timezone.utc)
        with self.database.session(write=True) as (connection, _):
            if automation_revision is not None:
                setting = connection.execute("SELECT enabled,revision FROM acquisition_automation WHERE id=1").fetchone()
                if not setting["enabled"] or setting["revision"] != automation_revision:
                    return None
            prior = connection.execute("SELECT * FROM representative_handoffs WHERE request_key=?", (body["request_key"],)).fetchone()
            if prior:
                if prior["request_hash"] != request_hash:
                    raise ValueError("This request key belongs to a different handoff")
                return {**self._handoff_record(prior), "reused": True}
            row = connection.execute("SELECT * FROM acquisition_briefs WHERE id=?", (body["brief_id"],)).fetchone()
            if not row:
                raise ValueError("Saved brief not found")
            if row["status"] == "running":
                raise ValueError("Wait for the brief to finish before preparing a handoff")
            brief = self._brief_record(row)
            cards = brief["result"].get("cards", [])
            if any(i >= len(cards) for i in indices):
                raise ValueError("Selected property is not in this saved brief")
            selected = [cards[i] for i in sorted(indices)]
            gaps = ["Confirm the intended legal client/buyer, funding and authorized decision-maker.",
                    "Verify the representative's license, brokerage, availability, conflicts and agreed fees.",
                    "Establish representation scope and separately approve any offer or financial commitment."]
            if row["completed_at"] < (now - timedelta(hours=24)).isoformat():
                gaps.append("Brief is over 24 hours old; refresh and recheck availability before relying on it.")
            for card in selected:
                if card.get("economics") is None:
                    gaps.append(f"{card['listing']['address']}: missing rent evidence; economics remain unscored.")
                elif card.get("screen") == "below_assumed_yield":
                    gaps.append(f"{card['listing']['address']}: below the assumed yield target; included for review, not as a qualifying match.")
            snapshot = {"client_name": body["client_name"], "representative_company": body["representative_company"],
                        "representative_contact": body["representative_contact"], "notes": body["notes"],
                        "brief_completed_at": brief["completed_at"], "criteria": brief["criteria"], "cards": selected,
                        "evidence": brief["result"].get("evidence", []), "warnings": brief["result"].get("warnings", []),
                        "coverage": brief["result"].get("coverage", ""), "basis": brief["result"].get("basis", ""),
                        "limitations": brief["result"].get("limitations", ""), "requested_tasks": TASKS,
                        "open_items": gaps, "boundary": BOUNDARY}
            encoded = _encoded(snapshot)
            handoff_id = str(uuid4())
            connection.execute("INSERT INTO representative_handoffs VALUES(?,?,?,?,?,?,?)",
                               (handoff_id, body["request_key"], request_hash, body["brief_id"], encoded,
                                sha256(encoded.encode()).hexdigest(), now.isoformat()))
            return self._handoff_record(connection.execute("SELECT * FROM representative_handoffs WHERE id=?", (handoff_id,)).fetchone())

    def representative_handoff(self, handoff_id):
        with self.database.session() as (connection, _):
            row = connection.execute("SELECT * FROM representative_handoffs WHERE id=?", (handoff_id,)).fetchone()
            if not row:
                raise LookupError("Saved handoff not found")
            return self._handoff_record(row)

    def representative_handoff_export(self, handoff_id):
        handoff = self.representative_handoff(handoff_id)
        snap = handoff["snapshot"]
        lines = ["CLUBSP — PROPOSED REPRESENTATIVE HANDOFF", "DRAFT / NOT SENT", snap["boundary"],
                 f"Prepared: {handoff['created_at']}", f"Intended client / buyer (unverified): {snap['client_name']}",
                 f"Proposed representative: {snap['representative_company']}",
                 f"Contact (user-entered, unverified): {snap['representative_contact'] or 'Not supplied'}",
                 f"Brief completed: {snap['brief_completed_at']}", "", "SCREENING ASSUMPTIONS"]
        labels = {"max_price": "Maximum asking price ($)", "min_beds": "Minimum bedrooms",
                  "repair_reserve": "Repair reserve ($)", "closing_pct": "Closing costs (%)",
                  "vacancy_pct": "Vacancy (%)", "expense_pct": "Expenses as % of collected rent",
                  "min_yield_pct": "Minimum operating yield (%)"}
        lines.extend(f"{label}: {snap['criteria'][key]}" for key, label in labels.items())
        for card in snap["cards"]:
            listing, rent, model = card["listing"], card.get("rent_estimate"), card.get("economics")
            lines.extend(["", ", ".join(str(listing.get(k)) for k in ("address", "city", "state", "zip") if listing.get(k)),
                          f"Asking price: ${listing['asking_price']:,.2f}; bedrooms: {listing.get('bedrooms', 'Unknown')}",
                          f"Screen: {card['screen']}"])
            lines.append(f"Provider monthly rent estimate: ${rent['estimate']:,.2f}; range ${rent['range_low']:,.2f}–${rent['range_high']:,.2f}" if rent else "Rent estimate unavailable; economics unscored.")
            if model:
                lines.append(f"Assumed cash basis: ${model['cash_basis']:,.2f}; annual operating income: ${model['annual_operating_income']:,.2f}; operating yield: {model['yield_pct']}%")
            lines.extend(card.get("next_actions", []))
        lines.extend(["", "REQUESTED WORK AFTER REPRESENTATION IS AGREED", *snap["requested_tasks"],
                      "", "OPEN ITEMS", *snap["open_items"], *snap["warnings"], "", "RESEARCH LIMITS",
                      snap["coverage"], snap["basis"], snap["limitations"], "", "SOURCE REFERENCES"])
        lines.extend(f"{e['source']} | {e['status']} | retrieved {e.get('retrieved_at') or 'unknown'} | internal record {e['run_id']}" for e in snap["evidence"])
        lines.extend(["Evidence is retained by ClubSP; these references do not grant access to the private owner workspace.",
                      "", "CLIENT NOTES", snap["notes"] or "None supplied", "", f"Packet ID: {handoff_id}",
                      f"Snapshot SHA-256: {handoff['snapshot_sha256']}"])
        return {"id": handoff_id, "filename": f"clubsp-handoff-{handoff_id}.txt", "text": "\n".join(lines)}
