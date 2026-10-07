"""Opt-in daily research and private drafts; never sends or transacts."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
import os
import threading
from uuid import uuid4
from zoneinfo import ZoneInfo

from .acquisition_briefs import criteria
from .representative_handoffs import _text, _encoded
from .schema import assert_component_compatible, ensure_component
from .sentra_runtime import _monthly_cap

ZONE = ZoneInfo("America/Indiana/Indianapolis")


def next_morning(now):
    local = now.astimezone(ZONE)
    due = local.replace(hour=9, minute=0, second=0, microsecond=0)
    if due <= local:
        due += timedelta(days=1)
    return due.astimezone(timezone.utc)


def changes(previous, current):
    def cards(brief):
        return {c["listing"]["address"].casefold(): c for c in brief.get("result", {}).get("cards", [])}
    old, new = cards(previous), cards(current)
    result = []
    for identity in sorted(old.keys() | new.keys()):
        card = new.get(identity, old.get(identity))
        address = card["listing"]["address"]
        if identity not in old:
            result.append({"address": address, "kind": "new_in_sample"})
        elif identity not in new:
            result.append({"address": address, "kind": "absent_from_sample"})
        else:
            for field, before, after in (
                ("asking_price", old[identity]["listing"].get("asking_price"), card["listing"].get("asking_price")),
                ("lower_rent", (old[identity].get("rent_estimate") or {}).get("range_low"), (card.get("rent_estimate") or {}).get("range_low")),
                ("assumed_yield", (old[identity].get("economics") or {}).get("yield_pct"), (card.get("economics") or {}).get("yield_pct"))):
                if before != after:
                    result.append({"address": address, "kind": field, "before": before, "after": after})
    return result


class AcquisitionAutomationMixin:
    def _initialize_acquisition_automation(self):
        with self.database.session(write=True) as (connection, _):
            assert_component_compatible(connection, "acquisition_automation")
            connection.execute("""CREATE TABLE IF NOT EXISTS acquisition_automation (
                id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 0, policy_json TEXT NOT NULL DEFAULT '{}',
                next_due TEXT NOT NULL DEFAULT '', lease_until TEXT NOT NULL DEFAULT '',
                token TEXT NOT NULL DEFAULT '')""")
            connection.execute("INSERT OR IGNORE INTO acquisition_automation(id) VALUES(1)")
            connection.execute("""CREATE TABLE IF NOT EXISTS acquisition_automation_runs (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}')""")
            ensure_component(connection, "acquisition_automation")

    def acquisition_automation_state(self):
        with self.database.session() as (connection, _):
            row = connection.execute("SELECT * FROM acquisition_automation WHERE id=1").fetchone()
            policy = json.loads(row["policy_json"])
            used = self._provider_usage(connection, "rentcast")["attempted_requests"]
            runs = [{"id": r["id"], "started_at": r["started_at"], "completed_at": r["completed_at"],
                     "status": r["status"], "result": json.loads(r["result_json"])} for r in connection.execute(
                         "SELECT * FROM acquisition_automation_runs ORDER BY started_at DESC,id DESC LIMIT 20")]
            return {"enabled": bool(row["enabled"]), "policy": policy, "next_due": row["next_due"],
                    "schedule": "Daily at 9 AM America/Indiana/Indianapolis", "runs": runs,
                    "handoff_ready": bool(policy.get("client_name") and policy.get("representative_company")),
                    "budget": {"used": used, "cap": _monthly_cap("rentcast"), "max_per_cycle": 4},
                    "external_actions": False}

    def save_acquisition_automation(self, data):
        allowed = {"enabled", "criteria", "generate_handoffs", "client_name", "representative_company",
                   "representative_contact", "notes", "confirm_external_request"}
        if not isinstance(data, dict) or set(data) - allowed or type(data.get("enabled")) is not bool:
            raise ValueError("Invalid automation settings")
        now = datetime.now(timezone.utc).isoformat()
        if data["enabled"]:
            if data.get("confirm_external_request") is not True:
                raise ValueError("Confirm daily provider requests within the existing monthly cap")
            if type(data.get("generate_handoffs", True)) is not bool:
                raise ValueError("Invalid draft setting")
            policy = {"criteria": criteria(data.get("criteria", {})), "generate_handoffs": data.get("generate_handoffs", True)}
            for key, limit in (("client_name", 200), ("representative_company", 200), ("representative_contact", 200), ("notes", 2000)):
                policy[key] = _text(data, key, limit, False)
            with self.database.session(write=True) as (connection, _):
                row = connection.execute("SELECT * FROM acquisition_automation WHERE id=1").fetchone()
                encoded = _encoded(policy)
                # Repeated clicks/reloads do not restart or duplicate an enabled schedule.
                if not row["enabled"] or row["policy_json"] != encoded:
                    connection.execute("UPDATE acquisition_automation SET enabled=1,revision=revision+1,policy_json=?,next_due=? WHERE id=1", (encoded, now))
        else:
            with self.database.session(write=True) as (connection, _):
                connection.execute("UPDATE acquisition_automation SET enabled=0,revision=revision+1 WHERE id=1")
        return self.acquisition_automation_state()

    def acquisition_automation_cycle(self, now=None):
        now = now or datetime.now(timezone.utc)
        stamp = now.isoformat()
        token = str(uuid4())
        with self.database.session(write=True) as (connection, _):
            row = connection.execute("SELECT * FROM acquisition_automation WHERE id=1").fetchone()
            if not row["enabled"] or row["next_due"] > stamp or row["lease_until"] > stamp:
                return None
            # Recover a crashed worker's durable run and idempotent brief/packet.
            token = row["token"] or token
            connection.execute("UPDATE acquisition_automation SET token=?,lease_until=? WHERE id=1",
                               (token, (now + timedelta(minutes=20)).isoformat()))
            connection.execute("INSERT OR IGNORE INTO acquisition_automation_runs(id,started_at,status) VALUES(?,?,'running')", (token, stamp))
            revision, policy = row["revision"], json.loads(row["policy_json"])
            latest = connection.execute("SELECT * FROM acquisition_briefs WHERE criteria_hash=? ORDER BY started_at DESC LIMIT 1",
                (sha256(json.dumps(policy["criteria"], sort_keys=True).encode()).hexdigest(),)).fetchone()
            previous = self._brief_record(latest) if latest else {}
            used = self._provider_usage(connection, "rentcast")["attempted_requests"]
        result = {}
        status = "completed"
        due = next_morning(now)
        try:
            disabled = os.environ.get("CLUBSP_SENTRAS_DISABLED", "").strip().lower()
            sources = os.environ.get("CLUBSP_DISABLED_SENTRAS", "").split(",")
            if disabled in {"1", "true", "yes", "on"} or any(s.strip() in {"rentcast_sale_listings", "rentcast_rent_estimate"} for s in sources):
                status = "paused_sources"
            elif not os.environ.get("RENTCAST_API_KEY", "").strip():
                status = "paused_credentials"
            elif not (latest and latest["status"] != "running" and latest["completed_at"] > (now - timedelta(hours=24)).isoformat()) and used + 4 > _monthly_cap("rentcast"):
                status = "paused_budget"
            else:
                brief = self.build_acquisition_brief(policy["criteria"])
                # A tick just before yesterday's completion must not reuse every other day.
                due = max(due, datetime.fromisoformat(brief["completed_at"]) + timedelta(hours=24, seconds=1))
                result = {"brief_id": brief["id"], "reused": bool(brief.get("reused")),
                          "changes": changes(previous, brief), "brief_status": brief["status"]}
                # Recheck after network work; packet transaction also checks this revision.
                with self.database.session(write=True) as (connection, _):
                    current = connection.execute("SELECT * FROM acquisition_automation WHERE id=1").fetchone()
                    active = current["enabled"] and current["revision"] == revision
                if not active:
                    status = "settings_changed"
                elif policy["generate_handoffs"]:
                    if not policy["client_name"] or not policy["representative_company"]:
                        result["handoff_status"] = "needs_client_and_representative"
                    else:
                        indices = [i for i, c in enumerate(brief["result"]["cards"])
                                   if c["decision"]["status"] == "review_candidate"]
                        if not indices:
                            result["handoff_status"] = "no_qualifying_cards"
                        else:
                            packet = {k: policy[k] for k in ("client_name", "representative_company", "representative_contact", "notes")}
                            key = "auto:" + sha256(_encoded({**packet, "brief_id": brief["id"], "card_indices": indices}).encode()).hexdigest()
                            handoff = self.prepare_representative_handoff({**packet, "brief_id": brief["id"], "card_indices": indices, "request_key": key}, automation_revision=revision)
                            if handoff:
                                result.update(handoff_id=handoff["id"], handoff_status="draft_not_sent")
                            else:
                                status = "settings_changed"
        except Exception as error:
            # Exception text can contain provider credentials or private response content.
            status, result = "retry_needed", {"error_type": type(error).__name__}
        with self.database.session(write=True) as (connection, _):
            connection.execute("UPDATE acquisition_automation_runs SET completed_at=?,status=?,result_json=? WHERE id=?",
                               (stamp, status, _encoded(result), token))
            connection.execute("UPDATE acquisition_automation SET lease_until='',token='',next_due=CASE WHEN revision=? THEN ? ELSE next_due END WHERE id=1 AND token=?",
                               (revision, due.isoformat(), token))
        return {"status": status, "result": result}


class AcquisitionAutomationScheduler:
    def __init__(self, application):
        self.application = application
        self.event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="clubsp-acquisition-automation", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.event.set()
        self.thread.join(timeout=1)

    def _run(self):
        while not self.event.is_set():
            try:
                self.application.acquisition_automation_cycle()
            except Exception as error:
                logging.getLogger(__name__).warning("Acquisition automation unavailable: %s", type(error).__name__)
            self.event.wait(30)
