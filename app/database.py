from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, fields
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from uuid import UUID

from core.memory import (
    Fact, LearningRecord, MemoryStore, Observation, Prediction, SourceRecord, WorkflowEvent,
)

COLLECTIONS = {
    "sources": SourceRecord,
    "facts": Fact,
    "predictions": Prediction,
    "observations": Observation,
    "learning_records": LearningRecord,
    "workflow_events": WorkflowEvent,
}
UUID_FIELDS = {"id", "subject_id", "source_id", "supersedes_fact_id", "deal_id"}
DATE_FIELDS = {"retrieved_at", "published_at", "observed_at", "created_at", "resolved_at", "learned_at"}


def json_default(value):
    if isinstance(value, (UUID, datetime)):
        return str(value) if isinstance(value, UUID) else value.isoformat()
    raise TypeError(f"Unsupported data type: {type(value).__name__}")


def dumps(value):
    return json.dumps(value, default=json_default, allow_nan=False)


def decode_record(model, body):
    data = json.loads(body)
    for item in fields(model):
        name = item.name
        if data.get(name) is not None:
            if name in UUID_FIELDS:
                data[name] = UUID(data[name])
            elif name in DATE_FIELDS:
                data[name] = datetime.fromisoformat(data[name])
            elif name == "evidence_refs":
                data[name] = [UUID(value) for value in data[name]]
    return model(**data)


class Database:
    """Each write reloads and commits a complete memory unit under a SQLite lock.

    This reference implementation favors correctness for a small local workspace.
    Large datasets should replace the full snapshot with targeted repository queries.
    """

    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS properties (
                    id TEXT PRIMARY KEY,
                    address TEXT NOT NULL,
                    city TEXT NOT NULL,
                    state TEXT NOT NULL,
                    zip TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS opportunity_policies (
                    id TEXT PRIMARY KEY, body TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deals (
                    id TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(id),
                    strategy TEXT NOT NULL CHECK(strategy IN ('assignment','resale')),
                    stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deal_events (
                    id TEXT PRIMARY KEY,
                    deal_id TEXT NOT NULL REFERENCES deals(id),
                    stage_before TEXT NOT NULL,
                    stage_after TEXT NOT NULL,
                    note TEXT NOT NULL,
                    evidence_reference TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS underwritings (
                    id TEXT PRIMARY KEY,
                    deal_id TEXT NOT NULL REFERENCES deals(id),
                    inputs_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS buyers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    company TEXT NOT NULL,
                    locations_json TEXT NOT NULL,
                    strategies_json TEXT NOT NULL,
                    property_types_json TEXT NOT NULL,
                    max_total_price REAL NOT NULL,
                    max_repairs REAL NOT NULL,
                    funding_status TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    verification_reference TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS buyer_match_runs (
                    id TEXT PRIMARY KEY,
                    deal_id TEXT NOT NULL REFERENCES deals(id),
                    matches_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory (
                    collection TEXT NOT NULL,
                    id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY (collection, id)
                );
                CREATE TABLE IF NOT EXISTS financial_plans (
                    id TEXT PRIMARY KEY, deal_id TEXT NOT NULL REFERENCES deals(id),
                    underwriting_id TEXT NOT NULL REFERENCES underwritings(id),
                    seller_price_cents INTEGER NOT NULL, assignment_fee_cents INTEGER NOT NULL,
                    planned_cash_at_risk_cents INTEGER NOT NULL, max_cash_at_risk_cents INTEGER NOT NULL,
                    offer_ceiling_cents INTEGER NOT NULL, desired_net_cents INTEGER NOT NULL,
                    basis TEXT NOT NULL, created_at TEXT NOT NULL, forecasts_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger_entries (
                    id TEXT PRIMARY KEY, deal_id TEXT NOT NULL REFERENCES deals(id),
                    entry_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, category TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0), occurred_on TEXT NOT NULL,
                    note TEXT NOT NULL, evidence_reference TEXT NOT NULL,
                    reversal_of TEXT UNIQUE REFERENCES ledger_entries(id), created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reconciliations (
                    id TEXT PRIMARY KEY, deal_id TEXT NOT NULL REFERENCES deals(id),
                    plan_id TEXT REFERENCES financial_plans(id), ledger_digest TEXT NOT NULL,
                    actual_net_cents INTEGER NOT NULL, forecast_net_cents INTEGER, variance_cents INTEGER,
                    evidence_reference TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, deal_id TEXT NOT NULL REFERENCES deals(id),
                    operation INTEGER NOT NULL CHECK(operation BETWEEN 1 AND 18), title TEXT NOT NULL,
                    owner TEXT NOT NULL, expected_result TEXT NOT NULL, due_on TEXT NOT NULL,
                    blocking_stage TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
                    system_key TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(deal_id,system_key)
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                    status_before TEXT NOT NULL, status_after TEXT NOT NULL, note TEXT NOT NULL,
                    evidence_reference TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ledger_deal ON ledger_entries(deal_id,occurred_on,created_at);
                CREATE INDEX IF NOT EXISTS tasks_deal ON tasks(deal_id,status);
                CREATE INDEX IF NOT EXISTS plans_deal ON financial_plans(deal_id,created_at);
                CREATE TABLE IF NOT EXISTS research_snapshots (
                    id TEXT PRIMARY KEY, property_id TEXT NOT NULL REFERENCES properties(id),
                    provider_id TEXT NOT NULL, parcel_key TEXT NOT NULL, status TEXT NOT NULL,
                    request_day TEXT NOT NULL, source_url TEXT NOT NULL, record_json TEXT NOT NULL,
                    identity_json TEXT NOT NULL, fact_ids_json TEXT NOT NULL, error TEXT NOT NULL,
                    review_note TEXT NOT NULL, reviewed_at TEXT, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS research_property ON research_snapshots(property_id,parcel_key,created_at);
                CREATE TABLE IF NOT EXISTS contacts (
                    id TEXT PRIMARY KEY, property_id TEXT NOT NULL REFERENCES properties(id),
                    name TEXT NOT NULL, email TEXT NOT NULL, role TEXT NOT NULL,
                    role_reference TEXT NOT NULL, permission_status TEXT NOT NULL,
                    permission_reference TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS contact_events (
                    id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id),
                    status_before TEXT NOT NULL, status_after TEXT NOT NULL,
                    note TEXT NOT NULL, evidence_reference TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS suppressions (
                    email TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id),
                    reason TEXT NOT NULL, evidence_reference TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id),
                    message_key TEXT NOT NULL UNIQUE, direction TEXT NOT NULL, channel TEXT NOT NULL,
                    category TEXT NOT NULL, body TEXT NOT NULL, evidence_reference TEXT NOT NULL,
                    occurred_on TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS seller_profiles (
                    id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id),
                    status TEXT NOT NULL, goal TEXT NOT NULL, timing TEXT NOT NULL,
                    condition_notes TEXT NOT NULL, authority_notes TEXT NOT NULL,
                    alternatives TEXT NOT NULL, priority TEXT NOT NULL, pain_points_json TEXT NOT NULL,
                    evidence_reference TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reply_drafts (
                    id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id),
                    message_id TEXT NOT NULL REFERENCES conversation_messages(id),
                    context_hash TEXT NOT NULL, context_json TEXT NOT NULL,
                    category TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
                    status TEXT NOT NULL, final_body TEXT NOT NULL, review_note TEXT NOT NULL,
                    reviewed_at TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS practice_attempts (
                    id TEXT PRIMARY KEY, attempt_key TEXT NOT NULL UNIQUE,
                    scenario_id TEXT NOT NULL, curriculum_version TEXT NOT NULL,
                    response TEXT NOT NULL, assessment_json TEXT NOT NULL,
                    review_note TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS contact_property ON contacts(property_id,created_at);
                CREATE INDEX IF NOT EXISTS message_contact ON conversation_messages(contact_id,created_at);
                CREATE INDEX IF NOT EXISTS profile_contact ON seller_profiles(contact_id,created_at);
                CREATE INDEX IF NOT EXISTS draft_contact ON reply_drafts(contact_id,created_at);
                CREATE TABLE IF NOT EXISTS knowledge_runs (
                    id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, source_ids_json TEXT NOT NULL,
                    jurisdiction TEXT NOT NULL, initiated_by TEXT NOT NULL, status TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS knowledge_snapshots (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES knowledge_runs(id),
                    source_id TEXT NOT NULL, domain TEXT NOT NULL, source_url TEXT NOT NULL,
                    status TEXT NOT NULL, data_json TEXT NOT NULL, diff_json TEXT NOT NULL,
                    checked_at TEXT, cached INTEGER NOT NULL DEFAULT 0, is_attempt INTEGER NOT NULL DEFAULT 0,
                    request_day TEXT NOT NULL, error TEXT NOT NULL, review_status TEXT NOT NULL,
                    review_note TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(run_id,source_id)
                );
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL UNIQUE REFERENCES knowledge_snapshots(id),
                    source_id TEXT NOT NULL, domain TEXT NOT NULL, title TEXT NOT NULL, claim TEXT NOT NULL,
                    claim_type TEXT NOT NULL, jurisdiction TEXT NOT NULL, published_on TEXT NOT NULL,
                    effective_on TEXT NOT NULL, applicability TEXT NOT NULL, reviewer TEXT NOT NULL,
                    review_reference TEXT NOT NULL, professional_review_reference TEXT NOT NULL,
                    supersedes_id TEXT REFERENCES knowledge_items(id), status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_events (
                    id TEXT PRIMARY KEY, item_id TEXT REFERENCES knowledge_items(id), snapshot_id TEXT REFERENCES knowledge_snapshots(id),
                    action TEXT NOT NULL, prior_item_id TEXT REFERENCES knowledge_items(id), reviewer TEXT NOT NULL,
                    note TEXT NOT NULL, evidence_reference TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS knowledge_source ON knowledge_snapshots(source_id,checked_at);
                CREATE INDEX IF NOT EXISTS knowledge_budget ON knowledge_snapshots(request_day,is_attempt);
                CREATE UNIQUE INDEX IF NOT EXISTS knowledge_active_source ON knowledge_items(source_id) WHERE status='active';
            """)

    @contextmanager
    def session(self, write=False):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            memory = MemoryStore()
            for row in connection.execute("SELECT collection, body FROM memory ORDER BY rowid"):
                model = COLLECTIONS[row["collection"]]
                record = decode_record(model, row["body"])
                getattr(memory, row["collection"])[record.id] = record
                if isinstance(record, Fact):
                    memory._by_subject[(record.subject_type, record.subject_id)].add(record.id)
            yield connection, memory
            if write:
                for collection in COLLECTIONS:
                    for record in getattr(memory, collection).values():
                        connection.execute(
                            "INSERT INTO memory(collection,id,body) VALUES(?,?,?) "
                            "ON CONFLICT(collection,id) DO UPDATE SET body=excluded.body",
                            (collection, str(record.id), dumps(asdict(record))),
                        )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
