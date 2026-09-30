from datetime import date, datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.memory.models import utc_now
from .validation import deal_exists, text_field


OPERATIONS = [
    ("Command and economics", "Set proposed terms, profit targets and your cash exposure limit.", "available"),
    ("Knowledge and research", "Refresh sourced business knowledge with an explicit review step.", "planned"),
    ("Market selection", "Choose a market using demand, cost and jurisdiction evidence.", "manual"),
    ("Buyer demand", "Record buy boxes and review current funding evidence.", "available"),
    ("Property sourcing", "Find and identify properties through approved data sources.", "planned"),
    ("Evidence and diligence", "Attach provenance and resolve ownership, condition and evidence gaps.", "available"),
    ("Qualification and pain points", "Confirm the seller's role, goal, constraints and fit without assumptions.", "manual"),
    ("Communication", "Use reviewed outreach channels and respect consent and suppression rules.", "planned"),
    ("Discovery and reply assistance", "Ground suggested replies in conversation and property evidence.", "planned"),
    ("Underwriting", "Compare exit price, repairs, costs, fees and downside at fixed proposed terms.", "available"),
    ("Sales training", "Practice objection handling using reviewed lessons and synthetic scenarios.", "planned"),
    ("Negotiation and offers", "Review terms and choose a bounded offer with owner authority.", "manual"),
    ("Contracts and title", "Have agreement rights, disclosures and title conditions reviewed.", "manual"),
    ("Disposition", "Match criteria, confirm buyer interest and prepare an authorized buyer packet.", "partial"),
    ("Closing", "Track conditions and dates; keep signatures separate from received money.", "manual"),
    ("Profit reconciliation", "Record receipts, costs and escrow, then compare actual contribution with the forecast.", "available"),
    ("Learning", "Use recorded outcomes and failure reasons to assess estimate quality.", "partial"),
    ("Reliability and control", "Keep audit events, retry-safe records and visible owner exceptions.", "partial"),
]
SYSTEM_TASKS = [
    ("evidence", 6, "Review property and ownership evidence", "Source references and ownership/condition gaps documented", "contracted"),
    ("seller_fit", 7, "Confirm seller role and fit", "Seller role, goal and constraints confirmed from evidence", "contracted"),
    ("underwriting", 10, "Record underwriting scenarios", "A saved assumption and scenario version", ""),
    ("money_plan", 1, "Record proposed terms and cash limit", "A financial plan tied to current underwriting", ""),
    ("agreement_review", 13, "Review agreement and title conditions", "Owner/professional review reference for terms, authority and title conditions", "contracted"),
    ("buyer_funding", 4, "Review buyer funding and closing ability", "Current evidence and buyer role/authority reviewed", "closing"),
    ("closing_evidence", 15, "Review closing completion evidence", "Closing conditions and settlement evidence reviewed", "completed"),
    ("reconcile", 16, "Reconcile actual costs and receipts", "Owner confirms a complete ledger with escrow resolved", ""),
]


def business_today():
    try:
        zone = ZoneInfo("America/Indiana/Indianapolis")
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    return datetime.now(zone).date()


class OperationsMixin:
    def _seed_tasks(self, connection, deal_id):
        for key, op, title, expected, gate in SYSTEM_TASKS:
            connection.execute(
                "INSERT OR IGNORE INTO tasks(id,deal_id,operation,title,owner,expected_result,due_on,blocking_stage,kind,status,system_key,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid4()), deal_id, op, title, "Owner", expected, "", gate, "task", "open", key,
                 utc_now().isoformat(), utc_now().isoformat()),
            )

    def _tasks(self, connection, deal_id):
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM tasks WHERE deal_id=? ORDER BY operation,created_at,id", (deal_id,)
        )]
        for row in rows:
            row["overdue"] = row["status"] == "open" and bool(row["due_on"]) and row["due_on"] < business_today().isoformat()
            row["events"] = [dict(r) for r in connection.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY created_at,id", (row["id"],))]
        return rows

    def _task_blockers(self, connection, deal_id, stage):
        if stage == "lost":
            return []
        return [row["title"] for row in connection.execute(
            "SELECT title FROM tasks WHERE deal_id=? AND status='open' AND blocking_stage IN (?, 'any') ORDER BY created_at,id", (deal_id, stage)
        )]

    def _complete_system_task(self, connection, deal_id, key, note, evidence):
        row = connection.execute("SELECT * FROM tasks WHERE deal_id=? AND system_key=?", (deal_id, key)).fetchone()
        if row is None or row["status"] == "done":
            return
        self._set_task_state(connection, row, "done", note, evidence)

    def _reopen_system_task(self, connection, deal_id, key, note):
        row = connection.execute("SELECT * FROM tasks WHERE deal_id=? AND system_key=?", (deal_id, key)).fetchone()
        if row and row["status"] == "done":
            self._set_task_state(connection, row, "open", note, "")

    @staticmethod
    def _set_task_state(connection, task, status, note, evidence):
        now = utc_now().isoformat()
        connection.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=?", (status, now, task["id"]))
        connection.execute(
            "INSERT INTO task_events(id,task_id,status_before,status_after,note,evidence_reference,created_at) VALUES(?,?,?,?,?,?,?)",
            (str(uuid4()), task["id"], task["status"], status, note, evidence, now),
        )

    def create_task(self, deal_id, data):
        title = text_field(data, "title", 200)
        owner = text_field(data, "owner", 120)
        expected = text_field(data, "expected_result", 1000)
        due_on = text_field(data, "due_on", 10, required=False)
        if due_on:
            try:
                if date.fromisoformat(due_on).isoformat() != due_on:
                    raise ValueError("Noncanonical date")
            except ValueError as exc:
                raise ValueError("due_on must be YYYY-MM-DD") from exc
        op = data.get("operation")
        if isinstance(op, bool) or not isinstance(op, int) or not 1 <= op <= 18:
            raise ValueError("operation must be a number from 1 to 18")
        gate = text_field(data, "blocking_stage", 30, required=False)
        kind = text_field(data, "kind", 20)
        if gate not in {"", "any", "contracted", "closing", "completed"} or kind not in {"task", "exception"}:
            raise ValueError("Unsupported task kind or stage gate")
        with self.database.session(write=True) as (connection, _):
            deal = deal_exists(connection, deal_id)
            now = utc_now().isoformat()
            record = {"id": str(uuid4()), "deal_id": str(deal["id"]), "operation": op,
                      "title": title, "owner": owner, "expected_result": expected,
                      "due_on": due_on, "blocking_stage": gate, "kind": kind, "status": "open",
                      "system_key": None, "created_at": now, "updated_at": now}
            connection.execute("INSERT INTO tasks(" + ",".join(record) + ") VALUES(" + ",".join("?" for _ in record) + ")", tuple(record.values()))
        return record

    def schedule_task(self, task_id, data):
        owner = text_field(data, "owner", 120)
        due = text_field(data, "due_on", 10, required=False)
        note = text_field(data, "note", 1000)
        if due:
            try:
                if date.fromisoformat(due).isoformat() != due:
                    raise ValueError("Noncanonical date")
            except ValueError as exc:
                raise ValueError("due_on must be YYYY-MM-DD") from exc
        with self.database.session(write=True) as (connection, _):
            row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise LookupError("Task not found")
            connection.execute("UPDATE tasks SET owner=?,due_on=? WHERE id=?", (owner, due, task_id))
            self._set_task_state(connection, row, row["status"], "Schedule: " + owner + ", " + (due or "no due date") + ". " + note, "")
        return {"id": task_id, "owner": owner, "due_on": due}

    def resolve_task(self, task_id, data):
        status = text_field(data, "status", 20)
        note = text_field(data, "note", 1000)
        evidence = text_field(data, "evidence_reference", 500, required=status == "done")
        if status not in {"open", "done"}:
            raise ValueError("Task status must be open or done")
        with self.database.session(write=True) as (connection, _):
            task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise LookupError("Task not found")
            if task["system_key"] in {"underwriting", "money_plan", "reconcile"}:
                raise ValueError("This task is updated by its recorded operation")
            if task["status"] != status:
                self._set_task_state(connection, task, status, note, evidence)
            return {"id": task["id"], "status": status}
