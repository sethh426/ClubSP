"""Reviewed transaction file for contracts, title conditions and closing evidence."""
from __future__ import annotations

from datetime import date
import hashlib
import json
from uuid import UUID, uuid4

from core.memory.models import utc_now
from .operations import business_today
from .validation import deal_exists, text_field


DOCUMENT_KINDS = {
    "purchase_agreement", "assignment_agreement", "amendment", "disclosure",
    "title_commitment", "closing_instructions", "settlement_statement", "other",
}
DOCUMENT_STATUSES = {"draft", "reviewed", "executed", "voided"}
SIGNATURE_STATUSES = {"not_signed", "partially_signed", "fully_signed", "not_applicable"}
TITLE_STATUSES = {"unknown", "open", "conditions_pending", "cleared_by_professional", "not_applicable"}
CONDITION_STATUSES = {"open", "satisfied", "waived_by_owner", "not_applicable"}
CLOSING_STATES = {
    "scheduled", "conditions_pending", "ready_by_professional",
    "signed", "funded_disbursed", "recorded_complete",
}
CLOSING_NEXT = {
    None: {"scheduled", "conditions_pending"},
    "scheduled": {"conditions_pending", "ready_by_professional"},
    "conditions_pending": {"scheduled", "ready_by_professional"},
    "ready_by_professional": {"conditions_pending", "signed"},
    "signed": {"conditions_pending", "funded_disbursed"},
    "funded_disbursed": {"recorded_complete"},
    "recorded_complete": set(),
}


def uid(data, key, required=True):
    value = text_field(data, key, 36, required=required)
    if not value:
        return None
    try:
        return str(UUID(value))
    except ValueError:
        raise ValueError(f"{key} must be a UUID") from None


def iso_day(data, key, required=False):
    value = text_field(data, key, 10, required=required)
    if not value:
        return ""
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
    except ValueError:
        raise ValueError(f"{key} must be YYYY-MM-DD") from None
    return value


def digest_context(deal_id, underwriting_id, financial_plan_id):
    return hashlib.sha256(
        json.dumps({
            "deal_id": deal_id,
            "underwriting_id": underwriting_id,
            "financial_plan_id": financial_plan_id,
        }, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class TransactionMixin:
    def _initialize_transactions(self):
        with self.database.session(write=True) as (connection, _):
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS transaction_documents (
                    id TEXT PRIMARY KEY,
                    deal_id TEXT NOT NULL REFERENCES deals(id),
                    request_key TEXT NOT NULL UNIQUE,
                    previous_id TEXT UNIQUE REFERENCES transaction_documents(id),
                    underwriting_id TEXT NOT NULL,
                    financial_plan_id TEXT NOT NULL,
                    context_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS transaction_documents_deal
                    ON transaction_documents(deal_id,created_at);

                CREATE TABLE IF NOT EXISTS transaction_conditions (
                    id TEXT PRIMARY KEY,
                    deal_id TEXT NOT NULL REFERENCES deals(id),
                    request_key TEXT NOT NULL UNIQUE,
                    previous_id TEXT UNIQUE REFERENCES transaction_conditions(id),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS transaction_conditions_deal
                    ON transaction_conditions(deal_id,created_at);

                CREATE TABLE IF NOT EXISTS closing_events (
                    id TEXT PRIMARY KEY,
                    deal_id TEXT NOT NULL REFERENCES deals(id),
                    request_key TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    evidence_reference TEXT NOT NULL,
                    professional_reference TEXT NOT NULL,
                    note TEXT NOT NULL,
                    occurred_on TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS closing_events_deal
                    ON closing_events(deal_id,created_at);
            """)

    @staticmethod
    def _decode(row):
        value = dict(row)
        value.update(json.loads(value.pop("payload_json")))
        return value

    def _current_context(self, connection, deal_id):
        deal = deal_exists(connection, deal_id)
        underwriting = connection.execute(
            "SELECT id FROM underwritings WHERE deal_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (deal["id"],),
        ).fetchone()
        plan = connection.execute(
            "SELECT id,underwriting_id FROM financial_plans WHERE deal_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (deal["id"],),
        ).fetchone()
        if not underwriting or not plan or plan["underwriting_id"] != underwriting["id"]:
            raise ValueError("Current underwriting and financial plan are required before transaction review")
        return deal, underwriting["id"], plan["id"]

    def save_transaction_document(self, deal_id, data):
        request_key = uid(data, "request_key")
        previous_id = uid(data, "previous_id", False)
        expected_uw = uid(data, "underwriting_id")
        expected_plan = uid(data, "financial_plan_id")
        payload = {
            "document_kind": text_field(data, "document_kind", 40),
            "document_reference": text_field(data, "document_reference", 500),
            "version_reference": text_field(data, "version_reference", 300),
            "status": text_field(data, "status", 20),
            "signature_status": text_field(data, "signature_status", 30),
            "title_status": text_field(data, "title_status", 40),
            "professional_review_reference": text_field(data, "professional_review_reference", 500, required=False),
            "title_review_reference": text_field(data, "title_review_reference", 500, required=False),
            "closing_professional": text_field(data, "closing_professional", 200, required=False),
            "effective_on": iso_day(data, "effective_on"),
            "expires_on": iso_day(data, "expires_on"),
            "note": text_field(data, "note", 2000),
            "owner_confirmed_review": data.get("owner_confirmed_review") is True,
        }
        if payload["document_kind"] not in DOCUMENT_KINDS:
            raise ValueError("Unsupported transaction document kind")
        if payload["status"] not in DOCUMENT_STATUSES:
            raise ValueError("Unsupported transaction document status")
        if payload["signature_status"] not in SIGNATURE_STATUSES:
            raise ValueError("Unsupported signature status")
        if payload["title_status"] not in TITLE_STATUSES:
            raise ValueError("Unsupported title status")
        if payload["expires_on"] and payload["effective_on"] and payload["expires_on"] < payload["effective_on"]:
            raise ValueError("Document expiry cannot predate its effective date")
        if payload["status"] in {"reviewed", "executed"} and not payload["owner_confirmed_review"]:
            raise ValueError("Owner must confirm review before marking a document reviewed or executed")
        if payload["status"] == "executed" and payload["signature_status"] != "fully_signed":
            raise ValueError("Executed document status requires fully signed evidence")
        if payload["title_status"] == "cleared_by_professional" and not payload["title_review_reference"]:
            raise ValueError("Professional title clearance requires a title review reference")
        if payload["document_kind"] in {"purchase_agreement", "assignment_agreement", "amendment", "disclosure", "title_commitment"} and payload["status"] in {"reviewed", "executed"} and not payload["professional_review_reference"]:
            raise ValueError("Reviewed/executed legal or title documents require a professional review reference")
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
        with self.database.session(write=True) as (connection, _):
            deal, current_uw, current_plan = self._current_context(connection, deal_id)
            if (expected_uw, expected_plan) != (current_uw, current_plan):
                raise ValueError("Deal economics changed; reload before saving the transaction document")
            existing = connection.execute(
                "SELECT * FROM transaction_documents WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing:
                if (existing["deal_id"], existing["previous_id"], existing["underwriting_id"],
                    existing["financial_plan_id"], existing["payload_json"]) != (
                        deal["id"], previous_id, expected_uw, expected_plan, encoded):
                    raise ValueError("request_key already records different transaction data")
                return self._decode(existing)
            latest = connection.execute(
                "SELECT id FROM transaction_documents WHERE deal_id=? AND json_extract(payload_json,'$.document_kind')=? "
                "ORDER BY rowid DESC LIMIT 1", (deal["id"], payload["document_kind"])
            ).fetchone()
            if previous_id != (latest["id"] if latest else None):
                raise ValueError("Refresh and supersede the latest version of this document kind")
            record_id = str(uuid4())
            context = digest_context(deal["id"], expected_uw, expected_plan)
            connection.execute(
                "INSERT INTO transaction_documents VALUES(?,?,?,?,?,?,?,?,?)",
                (record_id, deal["id"], request_key, previous_id, expected_uw, expected_plan,
                 context, encoded, utc_now().isoformat()),
            )
            return self._decode(connection.execute(
                "SELECT * FROM transaction_documents WHERE id=?", (record_id,)
            ).fetchone())

    def save_transaction_condition(self, deal_id, data):
        request_key = uid(data, "request_key")
        previous_id = uid(data, "previous_id", False)
        payload = {
            "name": text_field(data, "name", 240),
            "category": text_field(data, "category", 40),
            "status": text_field(data, "status", 30),
            "owner": text_field(data, "owner", 160),
            "due_on": iso_day(data, "due_on"),
            "evidence_reference": text_field(data, "evidence_reference", 500, required=False),
            "professional_reference": text_field(data, "professional_reference", 500, required=False),
            "note": text_field(data, "note", 2000),
        }
        if payload["category"] not in {"title", "contract", "buyer", "funding", "closing", "other"}:
            raise ValueError("Unsupported transaction condition category")
        if payload["status"] not in CONDITION_STATUSES:
            raise ValueError("Unsupported transaction condition status")
        if payload["status"] in {"satisfied", "waived_by_owner", "not_applicable"} and not payload["evidence_reference"]:
            raise ValueError("Resolved conditions require an evidence reference")
        if payload["category"] == "title" and payload["status"] == "satisfied" and not payload["professional_reference"]:
            raise ValueError("Satisfied title conditions require the closing/title professional reference")
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
        with self.database.session(write=True) as (connection, _):
            deal = deal_exists(connection, deal_id)
            existing = connection.execute(
                "SELECT * FROM transaction_conditions WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing:
                if (existing["deal_id"], existing["previous_id"], existing["payload_json"]) != (
                        deal["id"], previous_id, encoded):
                    raise ValueError("request_key already records different condition data")
                return self._decode(existing)
            latest = connection.execute(
                "SELECT id FROM transaction_conditions WHERE deal_id=? "
                "AND lower(json_extract(payload_json,'$.name'))=lower(?) ORDER BY rowid DESC LIMIT 1",
                (deal["id"], payload["name"]),
            ).fetchone()
            if previous_id != (latest["id"] if latest else None):
                raise ValueError("Refresh and supersede the latest version of this condition")
            record_id = str(uuid4())
            connection.execute(
                "INSERT INTO transaction_conditions VALUES(?,?,?,?,?,?)",
                (record_id, deal["id"], request_key, previous_id, encoded, utc_now().isoformat()),
            )
            return self._decode(connection.execute(
                "SELECT * FROM transaction_conditions WHERE id=?", (record_id,)
            ).fetchone())

    def record_closing_event(self, deal_id, data):
        request_key = uid(data, "request_key")
        state = text_field(data, "state", 30)
        evidence = text_field(data, "evidence_reference", 500)
        professional = text_field(data, "professional_reference", 500, required=False)
        note = text_field(data, "note", 2000)
        occurred_on = iso_day(data, "occurred_on", True)
        if state not in CLOSING_STATES:
            raise ValueError("Unsupported closing state")
        if date.fromisoformat(occurred_on) > business_today():
            raise ValueError("Closing event cannot be dated in the future")
        if state in {"ready_by_professional", "funded_disbursed", "recorded_complete"} and not professional:
            raise ValueError("This closing state requires a closing professional reference")
        with self.database.session(write=True) as (connection, _):
            deal = deal_exists(connection, deal_id)
            existing = connection.execute(
                "SELECT * FROM closing_events WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing:
                expected = (deal["id"], state, evidence, professional, note, occurred_on)
                actual = tuple(existing[key] for key in (
                    "deal_id", "state", "evidence_reference", "professional_reference", "note", "occurred_on"
                ))
                if actual != expected:
                    raise ValueError("request_key already records a different closing event")
                return dict(existing)
            latest_event = connection.execute(
                "SELECT state FROM closing_events WHERE deal_id=? ORDER BY rowid DESC LIMIT 1",
                (deal["id"],),
            ).fetchone()
            previous_state = latest_event["state"] if latest_event else None
            if state not in CLOSING_NEXT[previous_state]:
                raise ValueError(
                    "Closing state cannot move from "
                    + (previous_state or "not started") + " to " + state
                )
            open_conditions = connection.execute(
                "SELECT COUNT(*) FROM transaction_conditions tc "
                "WHERE tc.deal_id=? AND tc.rowid IN (SELECT MAX(rowid) FROM transaction_conditions WHERE deal_id=? GROUP BY json_extract(payload_json,'$.name')) "
                "AND json_extract(tc.payload_json,'$.status')='open'",
                (deal["id"], deal["id"]),
            ).fetchone()[0]
            if state in {"ready_by_professional", "signed", "funded_disbursed", "recorded_complete"} and open_conditions:
                raise ValueError("Open transaction conditions block this closing state")
            if state in {"signed", "funded_disbursed", "recorded_complete"}:
                _, current_uw, current_plan = self._current_context(connection, deal["id"])
                executed = connection.execute(
                    "SELECT 1 FROM transaction_documents WHERE deal_id=? "
                    "AND underwriting_id=? AND financial_plan_id=? "
                    "AND json_extract(payload_json,'$.status')='executed' "
                    "AND json_extract(payload_json,'$.signature_status')='fully_signed' "
                    "AND (json_extract(payload_json,'$.expires_on')='' OR json_extract(payload_json,'$.expires_on')>=?) "
                    "LIMIT 1",
                    (deal["id"], current_uw, current_plan, business_today().isoformat()),
                ).fetchone()
                if not executed:
                    raise ValueError("Record a fully signed executed transaction document first")
            record = {
                "id": str(uuid4()), "deal_id": deal["id"], "request_key": request_key,
                "state": state, "evidence_reference": evidence,
                "professional_reference": professional, "note": note,
                "occurred_on": occurred_on, "created_at": utc_now().isoformat(),
            }
            connection.execute(
                "INSERT INTO closing_events VALUES(?,?,?,?,?,?,?,?,?)",
                tuple(record.values()),
            )
            if state == "recorded_complete":
                self._complete_system_task(
                    connection, deal["id"], "closing_evidence",
                    "Closing completion evidence reviewed", evidence,
                )
            return record

    def _transaction_state(self, connection, deals):
        today = business_today().isoformat()
        result = {}
        for deal in deals:
            documents = [self._decode(row) for row in connection.execute(
                "SELECT * FROM transaction_documents WHERE deal_id=? ORDER BY rowid DESC", (deal["id"],)
            )]
            conditions = [self._decode(row) for row in connection.execute(
                "SELECT * FROM transaction_conditions WHERE deal_id=? ORDER BY rowid DESC", (deal["id"],)
            )]
            latest_conditions = {}
            for item in conditions:
                latest_conditions.setdefault(item["name"].casefold(), item)
            events = [dict(row) for row in connection.execute(
                "SELECT * FROM closing_events WHERE deal_id=? ORDER BY rowid DESC", (deal["id"],)
            )]
            current_uw = deal["underwriting"]["id"] if deal.get("underwriting") else None
            plan = deal["finance"]["plan"]
            current_plan = plan["id"] if plan else None
            for document in documents:
                document["context_current"] = bool(
                    current_uw and current_plan
                    and document["underwriting_id"] == current_uw
                    and document["financial_plan_id"] == current_plan
                )
                document["expired"] = bool(document["expires_on"] and document["expires_on"] < today)
            open_conditions = [c for c in latest_conditions.values() if c["status"] == "open"]
            result[deal["id"]] = {
                "documents": documents,
                "conditions": list(latest_conditions.values()),
                "closing_events": events,
                "latest_closing_state": events[0]["state"] if events else None,
                "open_condition_count": len(open_conditions),
                "open_conditions": open_conditions,
                "fully_signed_current_document": any(
                    d["status"] == "executed" and d["signature_status"] == "fully_signed"
                    and d["context_current"] and not d["expired"] for d in documents
                ),
                "execution_authorized": False,
                "title_clearance_is_professional_record_only": True,
                "funds_are_separate_from_signatures": True,
            }
        return result
