"""Registry resolution, durable bounded raw-source observations and readiness."""
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from uuid import uuid4

from .schema import assert_component_compatible, ensure_component
from .sentras import SENTRAS, SentraDefinition
from .sentra_registry import SentraRegistry, execution_blockers
from .sentra_execution import SentraExecutionRequest, canonical_payload_bytes, execute_sentra


TUPLE_FIELDS = {"capabilities", "downstream", "provenance_refs", "coverage_markets", "verified_capabilities"}


def definition_from_json(body):
    values = json.loads(body)
    for key in TUPLE_FIELDS:
        if key in values:
            values[key] = tuple(values[key])
    return SentraDefinition(**values)


def definition_revision(definition):
    return sha256(canonical_payload_bytes(asdict(definition))).hexdigest()


class SentraKernelMixin:
    def _initialize_sentra_kernel(self):
        with self.database.session(write=True) as (connection, _):
            assert_component_compatible(connection, "sentra_kernel")
            connection.execute("""CREATE TABLE IF NOT EXISTS sentra_kernel_runs (
                request_key TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE,
                sentra_id TEXT NOT NULL, candidate_fingerprint TEXT NOT NULL,
                definition_revision TEXT NOT NULL, definition_json TEXT NOT NULL, request_hash TEXT NOT NULL,
                reserved_requests INTEGER NOT NULL CHECK(reserved_requests > 0),
                status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}',
                error_code TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '', budget_day TEXT NOT NULL
            )""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_sentra_runs_budget ON sentra_kernel_runs(sentra_id,budget_day)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_sentra_runs_latest ON sentra_kernel_runs(sentra_id,started_at)")
            connection.execute("""CREATE TABLE IF NOT EXISTS sentra_kernel_definitions (
                id TEXT PRIMARY KEY, candidate_fingerprint TEXT NOT NULL UNIQUE,
                definition_json TEXT NOT NULL
            )""")
            ensure_component(connection, "sentra_kernel")

    def _kernel_definition_for_activation(self, connection, candidate, data):
        approval = connection.execute("""SELECT id FROM meta_source_events
            WHERE fingerprint=? AND to_state='approved' AND automated=0
            ORDER BY created_at DESC,id DESC LIMIT 1""", (candidate["fingerprint"],)).fetchone()
        if not approval:
            raise ValueError("a persisted explicit source approval is required")
        markets = data.get("coverage_markets", [data.get("jurisdiction")])
        if not isinstance(markets, list):
            raise ValueError("coverage_markets must be an array")
        definition = SentraDefinition(
            id=data["sentra_id"], name=candidate["name"], family=data["family"],
            acquisition_mode=data["acquisition_mode"], capabilities=tuple(json.loads(candidate["capabilities_json"])),
            jurisdiction=data["jurisdiction"], status="active", source_url=candidate["source_url"],
            freshness_target_hours=data.get("freshness_target_hours", 24), rights_note=data["rights_note"],
            source_type=data.get("source_type", "unclassified"), schema_fingerprint=candidate["schema_fingerprint"],
            provenance_refs=("catalog:" + candidate["discovery_provider"], "candidate:" + candidate["fingerprint"],
                             "schema:" + candidate["schema_fingerprint"], "meta_review:" + approval["id"]),
            rights_review_ref="meta_review:" + approval["id"],
            # Supported Meta-Sentra adapters make public, uncredentialed GET requests.
            # Metered accounts and paid Actor execution remain a separate gate.
            estimated_cost_class=data.get("estimated_cost_class", "free"),
            max_cost_per_run_cents=data.get("max_cost_per_run_cents", 0), daily_request_limit=data.get("daily_request_limit", 20),
            coverage_scope=data.get("coverage_scope", "market"), coverage_markets=tuple(markets),
        )
        connection.execute("""INSERT INTO sentra_kernel_definitions VALUES(?,?,?)
            ON CONFLICT(id) DO UPDATE SET candidate_fingerprint=excluded.candidate_fingerprint,definition_json=excluded.definition_json""",
            (definition.id, candidate["fingerprint"], json.dumps(asdict(definition), sort_keys=True)))
        return definition

    def _sentra_registry(self, connection):
        definitions = list(SENTRAS.values())
        for row in connection.execute("""SELECT a.*, c.state, k.definition_json FROM activated_sentras a
            JOIN meta_source_candidates c ON c.fingerprint=a.candidate_fingerprint
            LEFT JOIN sentra_kernel_definitions k ON k.id=a.id AND k.candidate_fingerprint=a.candidate_fingerprint
            WHERE c.state='active' ORDER BY a.id"""):
            if row["definition_json"]:
                definition = definition_from_json(row["definition_json"])
            else:
                # Legacy activation is visible but never silently gains execution rights.
                definition = SentraDefinition(
                    id=row["id"], name=row["name"], family=row["family"],
                    acquisition_mode=row["acquisition_mode"], capabilities=tuple(json.loads(row["capabilities_json"])),
                    jurisdiction=row["jurisdiction"], status="active", source_url=row["source_url"],
                    freshness_target_hours=24, rights_note=row["rights_note"],
                    provenance_refs=("candidate:" + row["candidate_fingerprint"],),
                )
            definitions.append(definition)
        return SentraRegistry(definitions)

    def sentra_registry(self):
        with self.database.session() as (connection, _):
            return self._sentra_registry(connection)

    def sentra_catalog_state(self, *, limit=100, offset=0):
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise ValueError("catalog pagination requires limit 1-200 and offset 0-1000000")
        with self.database.session() as (connection, _):
            registry = self._sentra_registry(connection)
            latest = {}
            for row in connection.execute("""SELECT r.* FROM sentra_kernel_runs r WHERE NOT EXISTS (
                SELECT 1 FROM sentra_kernel_runs n WHERE n.sentra_id=r.sentra_id
                AND (n.started_at>r.started_at OR (n.started_at=r.started_at AND n.run_id>r.run_id)))"""):
                latest[row["sentra_id"]] = dict(row)
        rows = []
        for definition in registry.definitions.values():
            run = latest.get(definition.id)
            record = definition.catalog_record()
            blockers = execution_blockers(definition)
            health = "never_run"
            if run:
                health = {"running": "in_progress", "success": "healthy", "failed": "unavailable", "discarded": "withheld"}[run["status"]]
                if health == "healthy" and definition.freshness_target_hours is not None:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(run["finished_at"])).total_seconds()
                    if age > definition.freshness_target_hours * 3600:
                        health = "stale"
            record.update({"execution_ready": not blockers, "execution_blockers": blockers,
                           "health": health, "last_attempt_at": run["started_at"] if run else None,
                           "last_result_status": run["status"] if run else None,
                           "evidence_scope": "raw_source", "normalized_records_ready": False})
            rows.append(record)
        return {"registry_revision": registry.revision, "sentras": sorted(rows, key=lambda r: r["id"])[offset:offset + limit],
                "summary": {"registered": len(rows), "execution_ready": sum(r["execution_ready"] for r in rows)},
                "pagination": {"limit": limit, "offset": offset, "has_more": len(rows) > offset + limit},
                "production_validation": "pending_live_canary"}

    def sentra_route(self, data):
        registry = self.sentra_registry()
        market = data.get("market")
        if type(data.get("verified_only", False)) is not bool:
            raise ValueError("verified_only must be a boolean")
        if market is not None and (not isinstance(market, str) or not market.strip() or len(market) > 200):
            raise ValueError("market must be bounded nonempty text")
        definitions = registry.route(data.get("capability"), market=market,
                                     verified_only=data.get("verified_only", False) is True)
        return {"registry_revision": registry.revision, "routes": [
            {**definition.catalog_record(), "execution_blockers": execution_blockers(definition),
             "evidence_scope": "raw_source"} for definition in definitions],
            "coverage_confirmed": bool(definitions) and data.get("verified_only") is True,
            "executed": False}

    def run_sentra(self, data):
        request_key = data.get("request_key")
        if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,120}", request_key):
            raise ValueError("request_key must be 8-120 letters, numbers, underscores or hyphens")
        request = SentraExecutionRequest(data.get("sentra_id"), operation=data.get("operation", "observe"),
                                        input_data=data.get("input_data", {}), idempotency_key=request_key,
                                        max_bytes=data.get("max_bytes", 262144))
        # Only raw observation is supported in this gate. No caller-controlled URL,
        # actor approval, query, credentials or search/lookup semantics are inferred.
        if request.input_data or request.operation not in {"observe", "refresh"}:
            raise ValueError("kernel runs support configured source observation only")
        if request.max_bytes > 262144:
            raise ValueError("kernel source observations are limited to 262144 bytes")
        request_hash = sha256(canonical_payload_bytes({"sentra_id": request.sentra_id,
            "operation": request.operation, "input_data": dict(request.input_data), "max_bytes": request.max_bytes})).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        run_id = str(uuid4())
        with self.database.session(write=True) as (connection, _):
            registry = self._sentra_registry(connection)
            definition = registry.get(request.sentra_id)
            revision = definition_revision(definition)
            blockers = execution_blockers(definition)
            if blockers:
                raise ValueError("Sentra execution blocked: " + ", ".join(blockers))
            existing = connection.execute("SELECT * FROM sentra_kernel_runs WHERE request_key=?", (request_key,)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash or existing["definition_revision"] != revision:
                    raise ValueError("request_key was already used with different inputs or registry configuration")
                return {"run_id": existing["run_id"], "status": existing["status"], "replayed": True,
                        "result": json.loads(existing["result_json"]), "error_code": existing["error_code"]}
            reserved_requests = 2 if definition.acquisition_mode == "arcgis" else 1
            used = connection.execute("SELECT COALESCE(SUM(reserved_requests),0) FROM sentra_kernel_runs WHERE sentra_id=? AND budget_day=?",
                                      (definition.id, now[:10])).fetchone()[0]
            if used + reserved_requests > definition.daily_request_limit:
                raise ValueError("Sentra daily request budget exhausted")
            candidate = connection.execute("SELECT candidate_fingerprint FROM activated_sentras WHERE id=?", (definition.id,)).fetchone()
            fingerprint = candidate[0] if candidate else ""
            connection.execute("""INSERT INTO sentra_kernel_runs(request_key,run_id,sentra_id,candidate_fingerprint,
                definition_revision,definition_json,request_hash,status,reserved_requests,started_at,budget_day)
                VALUES(?,?,?,?,?,?,?,'running',?,?,?)""",
                (request_key, run_id, definition.id, fingerprint, revision, json.dumps(asdict(definition), sort_keys=True),
                 request_hash, reserved_requests, now, now[:10]))
        status, result, error_code = "success", {}, ""
        try:
            if fingerprint:
                result = self._execute_meta_source({"sentra_id": definition.id}, max_bytes=request.max_bytes)
                result["metadata"].update({"evidence_scope": "raw_source",
                                           "raw_sha256": result["metadata"]["raw_payload_hash"]})
            else:
                result = asdict(execute_sentra(request, registry=registry))
            if result["metadata"].get("schema_fingerprint") != definition.schema_fingerprint:
                status, error_code = "discarded", "schema_drift"
                result = {}
            else:
                result["metadata"].update({"registry_definition_revision": revision,
                    "rights_review_ref": definition.rights_review_ref,
                    "provenance_refs": list(definition.provenance_refs),
                    "reserved_request_count": reserved_requests})
        except (ValueError, TypeError, UnicodeError, LookupError):
            status, error_code = "failed", "source_observation_failed"
        with self.database.session(write=True) as (connection, _):
            current = self._sentra_registry(connection).definitions.get(definition.id)
            if current is None or definition_revision(current) != revision:
                status, result, error_code = "discarded", {}, "registry_changed_during_run"
            connection.execute("UPDATE sentra_kernel_runs SET status=?,result_json=?,error_code=?,finished_at=? WHERE run_id=?",
                               (status, json.dumps(result, sort_keys=True, allow_nan=False), error_code,
                                datetime.now(timezone.utc).isoformat(), run_id))
            if fingerprint and error_code == "schema_drift" and current is not None:
                self._requarantine(connection, fingerprint, "Kernel observation detected schema drift")
        return {"run_id": run_id, "status": status, "result": result, "error_code": error_code, "replayed": False}
