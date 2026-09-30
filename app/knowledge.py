"""Button-only source checks, owner-reviewed knowledge versions and audit history."""
from datetime import date, datetime, timedelta
import json
import threading
from uuid import UUID, uuid4

from core.memory.models import utc_now
from .knowledge_sources import (SOURCE_MAP, SOURCES, GAPS, KnowledgeSourceAdapter,
                                DAILY_SOURCE_LIMIT, CACHE_HOURS, compare_snapshots)
from .operations import business_today
from .validation import text_field, list_field


def insert(connection, table, record):
    connection.execute("INSERT INTO " + table + "(" + ",".join(record) + ") VALUES(" + ",".join("?" for _ in record) + ")", tuple(record.values()))


def snapshot_json(row):
    item = dict(row)
    item["data"] = json.loads(item.pop("data_json"))
    item["diff"] = json.loads(item.pop("diff_json"))
    item["cached"] = bool(item["cached"])
    item["is_attempt"] = bool(item["is_attempt"])
    return item


class KnowledgeMixin:
    knowledge_adapter = None
    knowledge_inline = False

    def _recover_knowledge_runs(self, connection):
        # A restart never silently starts fresh external research.
        for row in connection.execute("SELECT id FROM knowledge_runs WHERE status IN ('requested','running')").fetchall():
            connection.execute("UPDATE knowledge_runs SET status='failed',finished_at=? WHERE id=?", (utc_now().isoformat(), row["id"]))
            connection.execute("UPDATE knowledge_snapshots SET status='failed',error='Interrupted by server restart; request a new update' WHERE run_id=? AND status IN ('queued','fetching')", (row["id"],))

    def _knowledge_run(self, connection, run_id):
        row = connection.execute("SELECT * FROM knowledge_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise LookupError("Knowledge run not found")
        run = dict(row)
        run["source_ids"] = json.loads(run.pop("source_ids_json"))
        run["cancel_requested"] = bool(run["cancel_requested"])
        run["snapshots"] = [snapshot_json(r) for r in connection.execute("SELECT * FROM knowledge_snapshots WHERE run_id=? ORDER BY created_at,id", (run_id,))]
        return run

    def start_knowledge_update(self, data):
        sources = list_field(data, "source_ids", allowed=SOURCE_MAP, max_items=8)
        jurisdiction = text_field(data, "jurisdiction", 160)
        initiated_by = text_field(data, "initiated_by", 120)
        try:
            key = str(UUID(text_field(data, "request_key", 36)))
        except ValueError as exc:
            raise ValueError("request_key must be a UUID") from exc
        with self.database.session(write=True) as (connection, _):
            existing = connection.execute("SELECT * FROM knowledge_runs WHERE request_key=?", (key,)).fetchone()
            if existing:
                if (json.loads(existing["source_ids_json"]), existing["jurisdiction"], existing["initiated_by"]) != (sources, jurisdiction, initiated_by):
                    raise ValueError("request_key already records a different knowledge request")
                return self._knowledge_run(connection, existing["id"])
            if connection.execute("SELECT id FROM knowledge_runs WHERE status IN ('requested','running')").fetchone():
                raise ValueError("A knowledge update is already in progress; wait or cancel it")
            now = utc_now().isoformat()
            run = {"id": str(uuid4()), "request_key": key, "source_ids_json": json.dumps(sources),
                   "jurisdiction": jurisdiction, "initiated_by": initiated_by, "status": "requested",
                   "cancel_requested": 0, "created_at": now, "finished_at": None}
            insert(connection, "knowledge_runs", run)
            for source_id in sources:
                source = SOURCE_MAP[source_id]
                insert(connection, "knowledge_snapshots", {"id": str(uuid4()), "run_id": run["id"],
                    "source_id": source_id, "domain": source["domain"], "source_url": source["url"],
                    "status": "queued", "data_json": "{}", "diff_json": "{}", "checked_at": None,
                    "cached": 0, "is_attempt": 0, "request_day": "", "error": "",
                    "review_status": "pending", "review_note": "", "created_at": now})
        if self.knowledge_inline:
            self._execute_knowledge_run(run["id"])
        else:
            threading.Thread(target=self._execute_knowledge_run, args=(run["id"],), daemon=True).start()
        with self.database.session() as (connection, _):
            return self._knowledge_run(connection, run["id"])

    def _check_knowledge_source(self, run_id, source_id):
        with self.database.session(write=True) as (connection, _):
            run = self._knowledge_run(connection, run_id)
            row = connection.execute("SELECT * FROM knowledge_snapshots WHERE run_id=? AND source_id=?", (run_id, source_id)).fetchone()
            if run["cancel_requested"] or run["status"] not in {"requested", "running"}:
                connection.execute("UPDATE knowledge_snapshots SET status='cancelled',error='Update cancelled or interrupted' WHERE id=? AND status='queued'", (row["id"],))
                return
            if row["status"] != "queued":
                return
            prior_row = connection.execute("SELECT * FROM knowledge_snapshots WHERE source_id=? AND status='succeeded' AND id!=? ORDER BY checked_at DESC,created_at DESC,id LIMIT 1", (source_id, row["id"])).fetchone()
            previous = snapshot_json(prior_row) if prior_row else None
            if previous and utc_now() - datetime.fromisoformat(previous["checked_at"]) < timedelta(hours=CACHE_HOURS):
                connection.execute("UPDATE knowledge_snapshots SET status='succeeded',data_json=?,diff_json=?,checked_at=?,cached=1 WHERE id=?", (json.dumps(previous["data"]), json.dumps(compare_snapshots(previous["data"], previous["data"])), previous["checked_at"], row["id"]))
                return
            day = business_today().isoformat()
            attempts = connection.execute("SELECT count(*) FROM knowledge_snapshots WHERE request_day=? AND is_attempt=1", (day,)).fetchone()[0]
            if attempts >= DAILY_SOURCE_LIMIT:
                connection.execute("UPDATE knowledge_snapshots SET status='failed',error='Daily knowledge-source request budget reached' WHERE id=?", (row["id"],))
                return
            connection.execute("UPDATE knowledge_snapshots SET status='fetching',is_attempt=1,request_day=? WHERE id=?", (day, row["id"]))
            snapshot_id = row["id"]
        try:
            result = (self.knowledge_adapter or KnowledgeSourceAdapter()).fetch(source_id)
            # Bound adapter output too; adapters cannot supply instructions or destinations.
            if not isinstance(result, dict) or set(result) != {"content_hash", "word_count", "section_hashes", "title", "excerpt", "reported_published_on"}:
                raise ValueError("Unexpected source snapshot format")
            if not isinstance(result["content_hash"], str) or len(result["content_hash"]) != 64:
                raise ValueError("Invalid source fingerprint")
            if isinstance(result["word_count"], bool) or not isinstance(result["word_count"], int) or result["word_count"] < 1:
                raise ValueError("Invalid source word count")
            if not isinstance(result["section_hashes"], list) or not 1 <= len(result["section_hashes"]) <= 500 or any(not isinstance(v, str) or len(v) != 64 for v in result["section_hashes"]):
                raise ValueError("Invalid source section fingerprints")
            for field, limit in (("title", 300), ("excerpt", 500), ("reported_published_on", 10)):
                if not isinstance(result[field], str) or len(result[field]) > limit:
                    raise ValueError("Invalid source preview")
            comparison = compare_snapshots(previous["data"] if previous else None, result)
        except Exception as error:
            with self.database.session(write=True) as (connection, _):
                connection.execute("UPDATE knowledge_snapshots SET status='failed',error=? WHERE id=?", ("Source unavailable or unreadable (" + type(error).__name__ + "). Active knowledge unchanged.", snapshot_id))
            return
        with self.database.session(write=True) as (connection, _):
            run = self._knowledge_run(connection, run_id)
            if run["cancel_requested"] or run["status"] not in {"requested", "running"}:
                connection.execute("UPDATE knowledge_snapshots SET status='cancelled',error='Update cancelled or interrupted; result not staged' WHERE id=?", (snapshot_id,))
                return
            connection.execute("UPDATE knowledge_snapshots SET status='succeeded',data_json=?,diff_json=?,checked_at=? WHERE id=?", (json.dumps(result, allow_nan=False), json.dumps(comparison), utc_now().isoformat(), snapshot_id))

    def _execute_knowledge_run(self, run_id):
        try:
            with self.database.session(write=True) as (connection, _):
                run = self._knowledge_run(connection, run_id)
                if run["status"] != "requested":
                    return
                connection.execute("UPDATE knowledge_runs SET status='running' WHERE id=?", (run_id,))
            for source_id in run["source_ids"]:
                self._check_knowledge_source(run_id, source_id)
            with self.database.session(write=True) as (connection, _):
                current = self._knowledge_run(connection, run_id)
                if current["status"] not in {"requested", "running"}:
                    return
                succeeded = sum(s["status"] == "succeeded" for s in current["snapshots"])
                status = "cancelled" if current["cancel_requested"] else (
                    "review_required" if succeeded == len(current["snapshots"]) else "partially_complete" if succeeded else "failed")
                connection.execute("UPDATE knowledge_runs SET status=?,finished_at=? WHERE id=?", (status, utc_now().isoformat(), run_id))
        except Exception:
            # Unexpected local failures remain visible; no automatic external retry.
            with self.database.session(write=True) as (connection, _):
                connection.execute("UPDATE knowledge_runs SET status='failed',finished_at=? WHERE id=?", (utc_now().isoformat(), run_id))
                connection.execute("UPDATE knowledge_snapshots SET status='failed',error='Local update interrupted; request a new run' WHERE run_id=? AND status IN ('queued','fetching')", (run_id,))

    def cancel_knowledge_update(self, run_id, data):
        if data != {}:
            raise ValueError("Cancellation takes an empty object")
        with self.database.session(write=True) as (connection, _):
            run = self._knowledge_run(connection, run_id)
            if run["status"] in {"requested", "running"}:
                connection.execute("UPDATE knowledge_runs SET cancel_requested=1 WHERE id=?", (run_id,))
            return self._knowledge_run(connection, run_id)

    def review_knowledge_snapshot(self, snapshot_id, data):
        decision = text_field(data, "decision", 20)
        if decision not in {"accept", "reject"}:
            raise ValueError("Knowledge review must accept or reject")
        reviewer = text_field(data, "reviewer", 120)
        note = text_field(data, "note", 1000)
        with self.database.session(write=True) as (connection, _):
            row = connection.execute("SELECT * FROM knowledge_snapshots WHERE id=?", (snapshot_id,)).fetchone()
            if row is None:
                raise LookupError("Knowledge snapshot not found")
            snapshot = snapshot_json(row)
            if snapshot["status"] != "succeeded":
                raise ValueError("Only a successfully checked source can be reviewed")
            if snapshot["review_status"] != "pending":
                if snapshot["review_status"] == "accepted" and decision == "accept":
                    item = connection.execute("SELECT * FROM knowledge_items WHERE snapshot_id=?", (snapshot_id,)).fetchone()
                    fields = ("title", "claim", "claim_type", "applicability", "review_reference", "professional_review_reference", "published_on", "effective_on")
                    if data.get("owner_verified_source") is True and item["reviewer"] == reviewer and snapshot["review_note"] == note and all(text_field(data, key, 2000, required=False) == item[key] for key in fields):
                        return dict(item)
                if snapshot["review_status"] == "rejected" and decision == "reject" and snapshot["review_note"] == note:
                    prior_review = connection.execute("SELECT reviewer FROM knowledge_events WHERE snapshot_id=? AND action='reject'", (snapshot_id,)).fetchone()
                    if prior_review and prior_review["reviewer"] == reviewer:
                        return {"id": snapshot_id, "review_status": "rejected"}
                raise ValueError("This source check already has an immutable review")
            if decision == "reject":
                connection.execute("UPDATE knowledge_snapshots SET review_status='rejected',review_note=? WHERE id=?", (note, snapshot_id))
                self._knowledge_event(connection, None, snapshot_id, "reject", None, reviewer, note, "")
                return {"id": snapshot_id, "review_status": "rejected"}
            if data.get("owner_verified_source") is not True:
                raise ValueError("Reviewer must open and compare the original source")
            if utc_now() - datetime.fromisoformat(snapshot["checked_at"]) > timedelta(days=7):
                raise ValueError("Source check is too old; request a new update")
            latest = connection.execute("SELECT data_json FROM knowledge_snapshots WHERE source_id=? AND status='succeeded' ORDER BY checked_at DESC,created_at DESC,id LIMIT 1", (snapshot["source_id"],)).fetchone()
            if json.loads(latest["data_json"])["content_hash"] != snapshot["data"]["content_hash"]:
                raise ValueError("A newer changed source check exists; review that version")
            source = SOURCE_MAP[snapshot["source_id"]]
            reference = text_field(data, "review_reference", 500)
            professional = text_field(data, "professional_review_reference", 500, required=source["domain"] == "compliance")
            title = text_field(data, "title", 200)
            claim = text_field(data, "claim", 2000)
            applicability = text_field(data, "applicability", 2000)
            claim_type = text_field(data, "claim_type", 30)
            if claim_type not in {"interpretation", "operational_note", "source_metadata"}:
                raise ValueError("Knowledge must be an internal reviewed note, interpretation or metadata")
            dates = {}
            for field in ("published_on", "effective_on"):
                value = text_field(data, field, 10, required=False)
                if value:
                    try:
                        if date.fromisoformat(value).isoformat() != value or (field == "published_on" and value > business_today().isoformat()):
                            raise ValueError("Noncanonical or future publish date")
                    except ValueError as exc:
                        raise ValueError(field + " must be a real YYYY-MM-DD date") from exc
                dates[field] = value
            run = self._knowledge_run(connection, snapshot["run_id"])
            prior = connection.execute("SELECT id FROM knowledge_items WHERE source_id=? AND status='active'", (source["id"],)).fetchone()
            if prior:
                connection.execute("UPDATE knowledge_items SET status='superseded' WHERE id=?", (prior["id"],))
            item = {"id": str(uuid4()), "snapshot_id": snapshot_id, "source_id": source["id"], "domain": source["domain"],
                    "title": title, "claim": claim, "claim_type": claim_type, "jurisdiction": run["jurisdiction"],
                    **dates, "applicability": applicability, "reviewer": reviewer, "review_reference": reference,
                    "professional_review_reference": professional, "supersedes_id": prior["id"] if prior else None,
                    "status": "active", "created_at": utc_now().isoformat()}
            insert(connection, "knowledge_items", item)
            connection.execute("UPDATE knowledge_snapshots SET review_status='accepted',review_note=? WHERE id=?", (note, snapshot_id))
            self._knowledge_event(connection, item["id"], snapshot_id, "publish", item["supersedes_id"], reviewer, note, reference)
            return item

    @staticmethod
    def _knowledge_event(connection, item_id, snapshot_id, action, prior_id, reviewer, note, evidence):
        insert(connection, "knowledge_events", {"id": str(uuid4()), "item_id": item_id, "snapshot_id": snapshot_id,
            "action": action, "prior_item_id": prior_id, "reviewer": reviewer, "note": note,
            "evidence_reference": evidence, "created_at": utc_now().isoformat()})

    def change_knowledge_version(self, item_id, data):
        action = text_field(data, "action", 20)
        reviewer = text_field(data, "reviewer", 120)
        note = text_field(data, "note", 1000)
        evidence = text_field(data, "evidence_reference", 500)
        if action not in {"activate", "withdraw"} or data.get("owner_reviewed") is not True:
            raise ValueError("Choose activate/withdraw with owner review")
        with self.database.session(write=True) as (connection, _):
            row = connection.execute("SELECT * FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise LookupError("Knowledge item not found")
            if action == "withdraw":
                if row["status"] != "active":
                    raise ValueError("Only an active version can be withdrawn")
                connection.execute("UPDATE knowledge_items SET status='withdrawn' WHERE id=?", (item_id,))
                prior = item_id
            else:
                active = connection.execute("SELECT id FROM knowledge_items WHERE source_id=? AND status='active'", (row["source_id"],)).fetchone()
                if active and active["id"] == item_id:
                    return dict(row)
                prior = active["id"] if active else None
                if active:
                    connection.execute("UPDATE knowledge_items SET status='superseded' WHERE id=?", (active["id"],))
                connection.execute("UPDATE knowledge_items SET status='active' WHERE id=?", (item_id,))
            self._knowledge_event(connection, item_id, row["snapshot_id"], action, prior, reviewer, note, evidence)
            return dict(connection.execute("SELECT * FROM knowledge_items WHERE id=?", (item_id,)).fetchone())

    def _knowledge_state(self, connection):
        runs = [self._knowledge_run(connection, r["id"]) for r in connection.execute("SELECT id FROM knowledge_runs ORDER BY created_at DESC,id")]
        items = []
        for r in connection.execute("SELECT * FROM knowledge_items ORDER BY created_at DESC,id"):
            item = dict(r)
            snapshot = snapshot_json(connection.execute("SELECT * FROM knowledge_snapshots WHERE id=?", (item["snapshot_id"],)).fetchone())
            latest = connection.execute("SELECT data_json,checked_at FROM knowledge_snapshots WHERE source_id=? AND status='succeeded' ORDER BY checked_at DESC,created_at DESC,id LIMIT 1", (item["source_id"],)).fetchone()
            item["source_url"] = snapshot["source_url"]
            item["source_checked_at"] = snapshot["checked_at"]
            item["source_changed_since_review"] = bool(latest and json.loads(latest["data_json"])["content_hash"] != snapshot["data"]["content_hash"])
            item["source_check_older_than_seven_days"] = bool(latest and utc_now() - datetime.fromisoformat(latest["checked_at"]) > timedelta(days=7))
            item["execution_policy_changed"] = False
            items.append(item)
        coverage = []
        for source in SOURCES:
            latest_attempt = connection.execute("SELECT * FROM knowledge_snapshots WHERE source_id=? ORDER BY created_at DESC,id LIMIT 1", (source["id"],)).fetchone()
            last_success = connection.execute("SELECT checked_at FROM knowledge_snapshots WHERE source_id=? AND status='succeeded' ORDER BY checked_at DESC LIMIT 1", (source["id"],)).fetchone()
            active = next((i for i in items if i["source_id"] == source["id"] and i["status"] == "active"), None)
            coverage.append({"source_id": source["id"], "domain": source["domain"],
                "latest_status": latest_attempt["status"] if latest_attempt else "not_checked",
                "last_successful_check": last_success["checked_at"] if last_success else None,
                "active_item_id": active["id"] if active else None})
        attempts = connection.execute("SELECT count(*) FROM knowledge_snapshots WHERE request_day=? AND is_attempt=1", (business_today().isoformat(),)).fetchone()[0]
        return {"sources": SOURCES, "coverage_gaps": GAPS, "runs": runs, "items": items, "coverage": coverage,
                "events": [dict(r) for r in connection.execute("SELECT * FROM knowledge_events ORDER BY created_at DESC,id")],
                "source_requests_today": attempts, "daily_source_limit": DAILY_SOURCE_LIMIT,
                "cache_hours": CACHE_HOURS, "paid_provider_spend": 0,
                "research_mode": "Explicit button only; source checks with owner-authored review, not automatic synthesis"}
