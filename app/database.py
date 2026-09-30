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
