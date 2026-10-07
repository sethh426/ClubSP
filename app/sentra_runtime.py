"""Durable execution, budgets, evidence, changes and health for fixed Sentras."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from uuid import uuid4
from zoneinfo import ZoneInfo

from .schema import assert_component_compatible, ensure_component
from .sentras import FIRST_25_SENTRA_IDS, SENTRAS, sentra_catalog
from .sentra_collectors import SOURCE_SPECS, SourceTransportFailure, compile_source_request
from .sentra_execution import SentraExecutionRequest, canonical_payload_bytes, execute_sentra


def _now():
    return datetime.now(timezone.utc)


def load_sentra_environment(path):
    """Load only Sentra configuration keys; never execute a dotenv file."""
    from pathlib import Path
    source = Path(path)
    if not source.exists():
        return
    allowed = {"RENTCAST_API_KEY", "REALESTATEAPI_API_KEY", "CLUBSP_RENTCAST_MONTHLY_REQUEST_CAP",
               "CLUBSP_REALESTATEAPI_MONTHLY_COUNT_CAP", "CLUBSP_SENTRAS_DISABLED", "CLUBSP_DISABLED_SENTRAS"}
    for line in source.read_text().splitlines():
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if separator and key in allowed:
            if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
                value = value[1:-1]
            os.environ.setdefault(key, value)


def _budget_group(definition):
    if definition.credential_env == "RENTCAST_API_KEY":
        return "rentcast"
    if definition.credential_env == "REALESTATEAPI_API_KEY":
        return "realestateapi"
    return definition.id


def _monthly_cap(group):
    env, default = {"rentcast": ("CLUBSP_RENTCAST_MONTHLY_REQUEST_CAP", 40),
                    "realestateapi": ("CLUBSP_REALESTATEAPI_MONTHLY_COUNT_CAP", 20)}[group]
    try:
        value = int(os.environ.get(env, "") or default)
    except ValueError as exc:
        raise ValueError("Provider monthly cap must be an integer") from exc
    if not 1 <= value <= 10000:
        raise ValueError("Provider monthly cap must be from 1 to 10000")
    return value


def _disabled(definition):
    return (os.environ.get("CLUBSP_SENTRAS_DISABLED", "").lower() in {"1", "true", "yes", "on"}
            or definition.id in {v.strip() for v in os.environ.get("CLUBSP_DISABLED_SENTRAS", "").split(",")})


def _run_record(row):
    result = dict(row)
    result["result"] = json.loads(result.pop("result_json"))
    result["change"] = json.loads(result.pop("change_json"))
    # Do not return operator-provided request keys in the catalog or evidence.
    result.pop("idempotency_key", None)
    return result


def _run_summary(row):
    record = _run_record(row)
    result = record.pop("result")
    record["result_summary"] = {"kind": result.get("payload", {}).get("kind"),
                                "record_count": len(result.get("payload", {}).get("records", [])),
                                "payload_sha256": result.get("payload_sha256"),
                                "duration_ms": result.get("duration_ms")}
    return record


class SentraRuntimeMixin:
    def _initialize_sentra_runtime(self):
        with self.database.session(write=True) as (connection, _):
            assert_component_compatible(connection, "sentra_runtime")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS sentra_runs (
                    id TEXT PRIMARY KEY, sentra_id TEXT NOT NULL, input_hash TEXT NOT NULL,
                    idempotency_key TEXT, budget_group TEXT NOT NULL,
                    reserved_requests INTEGER NOT NULL, business_day TEXT NOT NULL,
                    status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}',
                    change_json TEXT NOT NULL DEFAULT '{}', error_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(sentra_id,idempotency_key)
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_sentra_runs ON sentra_runs(sentra_id,created_at)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS sentra_health (
                    sentra_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'unverified',
                    last_success_id TEXT NOT NULL DEFAULT '', current_run_id TEXT NOT NULL DEFAULT '',
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    cooldown_until TEXT NOT NULL DEFAULT '', schema_fingerprint TEXT NOT NULL DEFAULT ''
                )
            """)
            for key in FIRST_25_SENTRA_IDS:
                connection.execute("INSERT OR IGNORE INTO sentra_health(sentra_id) VALUES(?)", (key,))
            ensure_component(connection, "sentra_runtime")

    def sentra_state(self):
        now = _now()
        metadata = {item["id"]: item for item in sentra_catalog()}
        rows = []
        with self.database.session() as (connection, _):
            for ordinal, key in enumerate(FIRST_25_SENTRA_IDS, 1):
                definition, spec = SENTRAS[key], SOURCE_SPECS[key]
                health = dict(connection.execute("SELECT * FROM sentra_health WHERE sentra_id=?", (key,)).fetchone())
                last = connection.execute("SELECT * FROM sentra_runs WHERE sentra_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (key,)).fetchone()
                success = connection.execute("SELECT * FROM sentra_runs WHERE id=?", (health["last_success_id"],)).fetchone()
                blockers = []
                if _disabled(definition):
                    blockers.append("disabled")
                if definition.credential_env and not os.environ.get(definition.credential_env, "").strip():
                    blockers.append("missing_credentials")
                if health["cooldown_until"] and health["cooldown_until"] > now.isoformat():
                    blockers.append("failure_cooldown")
                stale = bool(success and (now - datetime.fromisoformat(success["completed_at"])).total_seconds() > (definition.freshness_target_hours or 24) * 3600)
                rows.append({**metadata[key], "ordinal": ordinal, "collector_kind": spec.kind,
                             "required_inputs": list(spec.required_inputs), "max_records": spec.max_records,
                             "max_http_requests": spec.max_requests, "daily_request_cap": spec.daily_request_cap,
                             "budget_group": _budget_group(definition), "health": health["status"],
                             "consecutive_failures": health["consecutive_failures"], "stale": stale,
                             "last_run": _run_summary(last) if last else None,
                             "last_success_at": success["completed_at"] if success else None,
                             "readiness": "blocked" if blockers else "ready_for_explicit_run",
                             "blockers": blockers, "live_verified": bool(success), "automatic_execution": False})
        return {"sentras": rows, "summary": {"source_collectors": len(rows),
                    "live_verified": sum(row["live_verified"] for row in rows),
                    "blocked": sum(bool(row["blockers"]) for row in rows)},
                "meta_sentras_counted": False, "creates_deals": False, "automatic_execution": False}

    def sentra_evidence(self, run_id):
        with self.database.session() as (connection, _):
            row = connection.execute("SELECT * FROM sentra_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise LookupError("Sentra run not found")
            return _run_record(row)

    def run_sentra(self, data):
        if not isinstance(data, dict) or set(data) - {"sentra_id", "input_data", "operation", "confirm_external_request", "idempotency_key", "force_refresh", "max_bytes"}:
            raise ValueError("Unsupported Sentra run fields")
        key = data.get("sentra_id")
        if not isinstance(key, str) or key not in FIRST_25_SENTRA_IDS:
            raise ValueError("Unknown source collector")
        if data.get("confirm_external_request") is not True:
            raise ValueError("confirm_external_request must be true for a source run")
        for flag in ("force_refresh",):
            if flag in data and type(data[flag]) is not bool:
                raise ValueError(f"{flag} must be a boolean")
        idempotency = data.get("idempotency_key")
        if idempotency is not None and (not isinstance(idempotency, str) or not 1 <= len(idempotency) <= 100):
            raise ValueError("idempotency_key must contain 1-100 characters")
        request = SentraExecutionRequest(key, operation=data.get("operation", "observe"),
            input_data=data.get("input_data", {}), idempotency_key=idempotency, max_bytes=data.get("max_bytes", 2_000_000))
        definition, spec = SENTRAS[key], SOURCE_SPECS[key]
        compile_source_request(definition, request)
        if _disabled(definition):
            raise ValueError("This Sentra is disabled")
        # Missing configuration and invalid inputs never consume a request slot.
        if definition.credential_env and not os.environ.get(definition.credential_env, "").strip():
            raise ValueError(f"{definition.credential_env} is not configured")
        input_hash = sha256(canonical_payload_bytes({"id": key, "operation": request.operation,
                            "input": request.input_data, "max_bytes": request.max_bytes})).hexdigest()
        now = _now()
        now_text = now.isoformat()
        group = _budget_group(definition)
        cap = _monthly_cap(group) if definition.credential_env else None
        business_day = now.astimezone(ZoneInfo("America/Indiana/Indianapolis")).date().isoformat()
        run_id = str(uuid4())
        with self.database.session(write=True) as (connection, _):
            if idempotency:
                prior = connection.execute("SELECT * FROM sentra_runs WHERE sentra_id=? AND idempotency_key=?", (key, idempotency)).fetchone()
                if prior:
                    if prior["input_hash"] != input_hash:
                        raise ValueError("Idempotency key was already used for different inputs")
                    if prior["status"] == "running":
                        raise ValueError("This Sentra request is already running")
                    return {**_run_record(prior), "cached": True}
            health = dict(connection.execute("SELECT * FROM sentra_health WHERE sentra_id=?", (key,)).fetchone())
            if health["current_run_id"]:
                running = connection.execute("SELECT * FROM sentra_runs WHERE id=?", (health["current_run_id"],)).fetchone()
                if running and (now - datetime.fromisoformat(running["created_at"])).total_seconds() < 180:
                    raise ValueError("Another run of this Sentra is in progress")
                if running:
                    connection.execute("UPDATE sentra_runs SET status='interrupted',error_text='Execution lease expired',completed_at=? WHERE id=?", (now_text, running["id"]))
                connection.execute("UPDATE sentra_health SET current_run_id='' WHERE sentra_id=?", (key,))
            if health["cooldown_until"] and health["cooldown_until"] > now_text:
                raise ValueError("Sentra is cooling down after repeated failures")
            if idempotency is None and not data.get("force_refresh", False):
                prior = connection.execute("SELECT * FROM sentra_runs WHERE sentra_id=? AND input_hash=? AND status='success' ORDER BY created_at DESC,id DESC LIMIT 1", (key, input_hash)).fetchone()
                if prior and (now - datetime.fromisoformat(prior["completed_at"])).total_seconds() < (definition.freshness_target_hours or 24) * 3600:
                    return {**_run_record(prior), "cached": True}
            daily = connection.execute("SELECT COALESCE(SUM(reserved_requests),0) FROM sentra_runs WHERE sentra_id=? AND business_day=?", (key, business_day)).fetchone()[0]
            if daily + spec.max_requests > spec.daily_request_cap:
                raise ValueError("Daily source request cap reached")
            if cap is not None:
                # _provider_usage includes both old provider requests and new Sentra reservations.
                used = self._provider_usage(connection, group)["attempted_requests"]
                if used + spec.max_requests > cap:
                    raise ValueError("Shared monthly provider request cap reached")
            connection.execute("INSERT INTO sentra_runs(id,sentra_id,input_hash,idempotency_key,budget_group,reserved_requests,business_day,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                               (run_id, key, input_hash, idempotency, group, spec.max_requests, business_day, "running", now_text))
            connection.execute("UPDATE sentra_health SET current_run_id=? WHERE sentra_id=?", (run_id, key))
        try:
            result = execute_sentra(request, transport=getattr(self, "_sentra_transport", None))
        except Exception as exc:
            # Transport exceptions can embed headers/URLs. Persist a bounded,
            # non-sensitive category while retaining the last good evidence.
            error = ("Source execution failed: " + exc.code if isinstance(exc, SourceTransportFailure)
                     else "Source execution failed (" + type(exc).__name__ + ")")
            done = _now().isoformat()
            with self.database.session(write=True) as (connection, _):
                failures = connection.execute("SELECT consecutive_failures FROM sentra_health WHERE sentra_id=?", (key,)).fetchone()[0] + 1
                cooldown = (_now() + timedelta(hours=1)).isoformat() if failures >= 3 else ""
                connection.execute("UPDATE sentra_runs SET status='failed',error_text=?,completed_at=? WHERE id=?", (error, done, run_id))
                connection.execute("UPDATE sentra_health SET status='degraded',current_run_id='',consecutive_failures=?,cooldown_until=? WHERE sentra_id=?", (failures, cooldown, key))
            return {**self.sentra_evidence(run_id), "cached": False}
        done = _now().isoformat()
        with self.database.session(write=True) as (connection, _):
            health = connection.execute("SELECT * FROM sentra_health WHERE sentra_id=?", (key,)).fetchone()
            # Compare the same query only; a different parcel/market is not a change event.
            prior = connection.execute("SELECT * FROM sentra_runs WHERE sentra_id=? AND input_hash=? AND status='success' ORDER BY created_at DESC,id DESC LIMIT 1", (key, input_hash)).fetchone()
            previous = json.loads(prior["result_json"]) if prior else None
            old_keys = {row["record_key"] for row in previous["payload"]["records"]} if previous else set()
            new_keys = {row["record_key"] for row in result.payload["records"]}
            schema = result.metadata["schema_fingerprint"]
            schema_changed = bool(previous and old_keys and new_keys and previous["metadata"]["schema_fingerprint"] != schema)
            change = {"baseline": previous is None, "changed": bool(previous and previous["payload_sha256"] != result.payload_sha256),
                      "previous_run_id": prior["id"] if prior else None, "added_record_keys": sorted(new_keys - old_keys),
                      "removed_record_keys": sorted(old_keys - new_keys), "schema_changed": schema_changed,
                      "semantics": "Observation changes for the same query; no sale/ownership inference"}
            connection.execute("UPDATE sentra_runs SET status='success',result_json=?,change_json=?,completed_at=? WHERE id=?",
                               (json.dumps(asdict(result), sort_keys=True, allow_nan=False), json.dumps(change), done, run_id))
            connection.execute("UPDATE sentra_health SET status='healthy',last_success_id=?,current_run_id='',consecutive_failures=0,cooldown_until='',schema_fingerprint=? WHERE sentra_id=?", (run_id, schema, key))
        return {**self.sentra_evidence(run_id), "cached": False}
