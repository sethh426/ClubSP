"""Bounded evidence planning over explicitly approved public dynamic Sentras.

Weights express operator policy, not calibrated probabilities. Acquisitions are
staged here and never write property facts, deals, buyers or messages.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re
import time
from uuid import UUID, uuid4

from .schema import assert_component_compatible, ensure_component

MAX_PROFILES = 10


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return value


def label(value, name, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} is required and must be at most {limit} characters")
    return value.strip()


def capabilities(value, name="capabilities"):
    if (not isinstance(value, list) or not 1 <= len(value) <= 12 or
            any(not isinstance(c, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", c) for c in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{name} must contain 1-12 distinct capability names")
    return sorted(value)


def score(value):
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 < value <= 1:
        raise ValueError("confidence must be finite and greater than zero, up to one")
    return float(value)


def records(payload):
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("features"), list):
            rows = [f.get("attributes", {}) for f in payload["features"] if isinstance(f, dict)]
        else:
            rows = next((payload[k] for k in ("records", "items", "results") if isinstance(payload.get(k), list)), [payload])
    else:
        rows = []
    return [r for r in rows if isinstance(r, dict)]


def normalize(payload, policy, subject):
    matched = [r for r in records(payload) if str(r.get(policy["identity_field"], "")).strip() == subject]
    claims = {}
    for capability, fields in policy["field_map"].items():
        values = [{f: r[f] for f in fields} for r in matched
                  if all(f in r and r[f] is not None and r[f] != "" for f in fields)]
        if values:
            claims[capability] = sorted({canonical(v): v for v in values}.values(), key=canonical)
    return {"subject": subject, "matched_records": len(matched), "claims": claims}


def aggregate(snapshots):
    scores, values, lineage = {}, {}, {}
    for snapshot in snapshots:
        for cap, value in snapshot["normalized"]["claims"].items():
            scores[cap] = max(scores.get(cap, 0), snapshot["confidence"])
            values.setdefault(cap, set()).add(canonical(value))
            lineage.setdefault(cap, []).append(snapshot["id"])
    conflicts = sorted(cap for cap, distinct in values.items() if len(distinct) > 1)
    for cap in conflicts:
        scores[cap] = 0
    return scores, conflicts, lineage


def cheapest_sequence(profiles, requirements, threshold, initial, budget, max_calls):
    """Exact subset dynamic program (at most 10 sources); dependencies are ordered."""
    states = {0: (dict(initial), (), 0, 0)}
    winners, partial = [], (0, 0, 0, (), dict(initial))
    for mask in range(1 << len(profiles)):
        if mask not in states:
            continue
        scores, sequence, cost, latency = states[mask]
        achieved = sum(scores.get(cap, 0) >= threshold for cap in requirements)
        candidate = (-achieved, cost, len(sequence), sequence, scores)
        if candidate[:4] < partial[:4]:
            partial = candidate
        if achieved == len(requirements):
            winners.append((cost, latency, len(sequence), sequence, scores))
            continue
        if len(sequence) >= max_calls:
            continue
        for index, p in enumerate(profiles):
            if mask & (1 << index) or cost + p["cost_cents"] > budget:
                continue
            if any(scores.get(cap, 0) < threshold for cap in p["requires"]):
                continue
            updated = dict(scores)
            for cap in p["field_map"]:
                updated[cap] = max(updated.get(cap, 0), p["confidence"])
            new = (updated, sequence + (p["sentra_id"],), cost + p["cost_cents"], latency + p["latency_ms"])
            newmask = mask | (1 << index)
            old = states.get(newmask)
            if old is None or (new[3], new[1]) < (old[3], old[1]):
                states[newmask] = new
    if winners:
        cost, latency, _, sequence, scores = min(winners)
        return {"sequence": list(sequence), "cost_cents": cost, "predicted_scores": scores, "satisfiable": True}
    sequence = partial[3]
    return {"sequence": list(sequence), "cost_cents": partial[1], "predicted_scores": partial[4], "satisfiable": False}


class EvidenceCompilerMixin:
    def _initialize_evidence_compiler(self):
        with self.database.session(write=True) as (c, _):
            assert_component_compatible(c, "evidence_compiler")
            c.execute("""CREATE TABLE IF NOT EXISTS evidence_profiles (
                sentra_id TEXT PRIMARY KEY, activation TEXT NOT NULL, body TEXT NOT NULL,
                version TEXT NOT NULL, updated_at REAL NOT NULL
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS evidence_runs (
                id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
                request_json TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT NOT NULL,
                started_at REAL NOT NULL, finished_at REAL, lease_until REAL NOT NULL
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS evidence_profile_versions (
                version TEXT PRIMARY KEY, sentra_id TEXT NOT NULL, activation TEXT NOT NULL,
                body TEXT NOT NULL, reviewed_at REAL NOT NULL
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS evidence_snapshots (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES evidence_runs(id),
                sentra_id TEXT NOT NULL, activation TEXT NOT NULL, profile_version TEXT NOT NULL,
                subject TEXT NOT NULL, confidence REAL NOT NULL, source_url TEXT NOT NULL,
                payload_hash TEXT NOT NULL, raw_hash TEXT NOT NULL, normalized_json TEXT NOT NULL,
                payload_json TEXT NOT NULL, retrieved_at REAL NOT NULL, duration_ms INTEGER NOT NULL
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS evidence_cache ON evidence_snapshots(sentra_id,subject,retrieved_at)")
            c.execute("""CREATE TABLE IF NOT EXISTS evidence_attempts (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES evidence_runs(id), sentra_id TEXT NOT NULL,
                cost_cents INTEGER NOT NULL, status TEXT NOT NULL, error TEXT NOT NULL,
                snapshot_id TEXT, created_at REAL NOT NULL
            )""")
            ensure_component(c, "evidence_compiler")

    def evidence_profile(self, data):
        if data.get("owner_reviewed") is not True:
            raise ValueError("source mappings and cost policy require owner_reviewed=true")
        row = self._active_source(data.get("sentra_id"))
        field_map = data.get("field_map")
        if not isinstance(field_map, dict):
            raise ValueError("field_map is required")
        caps = capabilities(list(field_map))
        if not set(caps).issubset(json.loads(row["capabilities_json"])):
            raise ValueError("field_map must use the source's approved capabilities")
        fields = set(json.loads(row["probe_json"]).get("fields", []))
        identity = label(data.get("identity_field"), "identity_field")
        if identity not in fields:
            raise ValueError("identity_field is not in the approved schema")
        for cap, mapped in field_map.items():
            if (not isinstance(mapped, list) or not 1 <= len(mapped) <= 8 or
                    any(not isinstance(f, str) or f not in fields for f in mapped) or len(set(mapped)) != len(mapped)):
                raise ValueError("mapped fields must be distinct fields in the approved schema")
        requires = capabilities(data["requires"], "requires") if data.get("requires") else []
        if set(requires) & set(caps):
            raise ValueError("a source cannot require its own capabilities")
        policy = {"sentra_id": row["id"], "identity_field": identity, "field_map": field_map,
                  "requires": requires, "confidence": score(data.get("confidence", 0.5)),
                  "cost_cents": integer(data.get("cost_cents", 0), "cost_cents", 0, 100000),
                  "latency_ms": integer(data.get("latency_ms", 1000), "latency_ms", 0, 60000),
                  "note": label(data.get("note"), "note", 4000)}
        version = digest({"activation": row["activated_at"], "policy": policy})
        with self.database.session(write=True) as (c, _):
            current = c.execute("SELECT activated_at FROM activated_sentras WHERE id=?", (row["id"],)).fetchone()
            if not current or current[0] != row["activated_at"]:
                raise ValueError("source activation changed during profile review")
            count = c.execute("SELECT count(*) FROM evidence_profiles p JOIN activated_sentras a ON a.id=p.sentra_id AND a.activated_at=p.activation WHERE p.sentra_id!=?", (row["id"],)).fetchone()[0]
            if count >= MAX_PROFILES:
                raise ValueError("this exact planner supports at most 10 reviewed source profiles")
            c.execute("INSERT INTO evidence_profiles VALUES(?,?,?,?,?) ON CONFLICT(sentra_id) DO UPDATE SET activation=excluded.activation,body=excluded.body,version=excluded.version,updated_at=excluded.updated_at",
                      (row["id"], row["activated_at"], canonical(policy), version, time.time()))
            c.execute("INSERT OR IGNORE INTO evidence_profile_versions VALUES(?,?,?,?,?)",
                      (version, row["id"], row["activated_at"], canonical(policy), time.time()))
        return {**policy, "version": version, "activation": row["activated_at"]}

    def _evidence_request(self, data):
        return {"subject": label(data.get("subject"), "subject"),
                "jurisdiction": label(data.get("jurisdiction"), "jurisdiction"),
                "capabilities": capabilities(data.get("capabilities")),
                "threshold": score(data.get("threshold", 0.5)),
                "max_cost_cents": integer(data.get("max_cost_cents", 0), "max_cost_cents", 0, 1000000),
                "max_calls": integer(data.get("max_calls", 5), "max_calls", 1, 10),
                "max_age_hours": integer(data.get("max_age_hours", 24), "max_age_hours", 1, 720)}

    def _evidence_inputs(self, request, now=None):
        now = time.time() if now is None else now
        profiles, cached = [], []
        with self.database.session() as (c, _):
            rows = c.execute("""SELECT p.*,a.jurisdiction,a.freshness_target_hours FROM evidence_profiles p
                JOIN activated_sentras a ON a.id=p.sentra_id AND a.activated_at=p.activation
                JOIN meta_source_candidates m ON m.fingerprint=a.candidate_fingerprint AND m.state='active'
                WHERE lower(a.jurisdiction)=lower(?) ORDER BY p.sentra_id""", (request["jurisdiction"],)).fetchall()
            for row in rows:
                profile = {**json.loads(row["body"]), "version": row["version"], "activation": row["activation"]}
                profiles.append(profile)
                maximum_age = min(request["max_age_hours"], row["freshness_target_hours"]) * 3600
                changed = c.execute("SELECT last_change_at FROM temporal_sources WHERE sentra_id=? AND activation=?",
                                    (row["sentra_id"], row["activation"])).fetchone()
                earliest = max(now - maximum_age, changed[0] or 0) if changed else now - maximum_age
                saved = c.execute("""SELECT * FROM evidence_snapshots WHERE sentra_id=? AND activation=?
                    AND profile_version=? AND subject=? AND retrieved_at>=? ORDER BY retrieved_at DESC,id DESC LIMIT 1""",
                    (row["sentra_id"], row["activation"], row["version"], request["subject"], earliest)).fetchone()
                if saved:
                    cached.append(self._decode_evidence_snapshot(saved))
        return profiles, cached

    @staticmethod
    def _decode_evidence_snapshot(row):
        item = dict(row)
        item["normalized"] = json.loads(item.pop("normalized_json"))
        item.pop("payload_json", None)
        return item

    def evidence_plan(self, data):
        request = self._evidence_request(data)
        profiles, cached = self._evidence_inputs(request)
        current, conflicts, lineage = aggregate(cached)
        # Contradictory cached evidence needs review; gathering more is not a resolution.
        plan = cheapest_sequence(profiles, request["capabilities"], request["threshold"], current,
                                 request["max_cost_cents"], request["max_calls"])
        return {"request": request, **plan, "cached_scores": current, "conflicts": conflicts,
                "cached_snapshot_ids": [s["id"] for s in cached], "lineage": lineage,
                "confidence_basis": "operator policy weights; not a calibrated probability",
                "facts_imported": False}

    def evidence_run(self, data):
        request = self._evidence_request(data)
        try:
            request_key = str(UUID(str(data.get("request_key"))))
        except (ValueError, TypeError):
            raise ValueError("request_key must be a UUID") from None
        request_hash, now = digest(request), time.time()
        with self.database.session(write=True) as (c, _):
            previous = c.execute("SELECT * FROM evidence_runs WHERE request_key=?", (request_key,)).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise ValueError("request_key already belongs to different inputs")
                if previous["status"] == "running":
                    if previous["lease_until"] < now:
                        c.execute("UPDATE evidence_runs SET status='interrupted',finished_at=? WHERE id=?", (now, previous["id"]))
                        return {"id": previous["id"], "status": "interrupted", "retry_requires_new_key": True}
                    return {"id": previous["id"], "status": "running"}
                return {"id": previous["id"], "status": previous["status"], **json.loads(previous["result_json"])}
            run_id = str(uuid4())
            c.execute("INSERT INTO evidence_runs VALUES(?,?,?,?,?,?,?,?,?)", (run_id, request_key, request_hash,
                      canonical(request), "running", "{}", now, None, now + 1800))
        profiles, snapshots = self._evidence_inputs(request)
        spent, attempts, used, failures = 0, 0, set(), []
        try:
            while True:
                achieved, conflicts, lineage = aggregate(snapshots)
                if conflicts:
                    status = "review_required"
                    break
                if all(achieved.get(cap, 0) >= request["threshold"] for cap in request["capabilities"]):
                    status = "sufficient"
                    break
                available = [p for p in profiles if p["sentra_id"] not in used]
                plan = cheapest_sequence(available, request["capabilities"], request["threshold"], achieved,
                                         request["max_cost_cents"] - spent, request["max_calls"] - attempts)
                if not plan["sequence"]:
                    status = "incomplete"
                    break
                sentra_id = plan["sequence"][0]
                policy = next(p for p in available if p["sentra_id"] == sentra_id)
                used.add(sentra_id)
                attempt_id = str(uuid4())
                with self.database.session(write=True) as (c, _):
                    c.execute("INSERT INTO evidence_attempts VALUES(?,?,?,?,?,?,?,?)", (attempt_id, run_id,
                              sentra_id, policy["cost_cents"], "reserved", "", None, time.time()))
                spent += policy["cost_cents"]
                attempts += 1
                try:
                    result = self.meta_execute({"sentra_id": sentra_id})
                    normalized = normalize(result["payload"], policy, request["subject"])
                    snapshot_id = str(uuid4())
                    with self.database.session(write=True) as (c, _):
                        current = c.execute("SELECT activated_at FROM activated_sentras WHERE id=?", (sentra_id,)).fetchone()
                        if not current or current[0] != policy["activation"]:
                            raise ValueError("source activation changed")
                        saved = c.execute("SELECT version FROM evidence_profiles WHERE sentra_id=?", (sentra_id,)).fetchone()
                        if not saved or saved[0] != policy["version"]:
                            raise ValueError("source policy changed")
                        c.execute("INSERT INTO evidence_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                            snapshot_id, run_id, sentra_id, policy["activation"], policy["version"], request["subject"],
                            policy["confidence"], result["source_url"], result["payload_sha256"], result["metadata"]["raw_payload_hash"],
                            canonical(normalized), canonical(result["payload"]), time.time(), result["duration_ms"]))
                        c.execute("UPDATE evidence_attempts SET status='success',snapshot_id=? WHERE id=?", (snapshot_id, attempt_id))
                        snapshots = [s for s in snapshots if s["sentra_id"] != sentra_id]
                        snapshots.append(self._decode_evidence_snapshot(c.execute("SELECT * FROM evidence_snapshots WHERE id=?", (snapshot_id,)).fetchone()))
                except (ValueError, LookupError, OSError) as exc:
                    error = type(exc).__name__ + "; inspect source history"
                    failures.append({"sentra_id": sentra_id, "error": error})
                    with self.database.session(write=True) as (c, _):
                        c.execute("UPDATE evidence_attempts SET status='failed',error=? WHERE id=?", (error, attempt_id))
            output = {"request": request, "scores": achieved, "conflicts": conflicts, "lineage": lineage,
                      "snapshot_ids": [s["id"] for s in snapshots], "calls": attempts,
                      "budgeted_cost_cents": spent, "failures": failures, "facts_imported": False,
                      "confidence_basis": "operator policy weights; not a calibrated probability"}
        except Exception:
            with self.database.session(write=True) as (c, _):
                c.execute("UPDATE evidence_runs SET status='interrupted',finished_at=? WHERE id=?", (time.time(), run_id))
            raise
        with self.database.session(write=True) as (c, _):
            c.execute("UPDATE evidence_runs SET status=?,result_json=?,finished_at=?,lease_until=0 WHERE id=?",
                      (status, canonical(output), time.time(), run_id))
        return {"id": run_id, "status": status, **output}

    def evidence_state(self):
        with self.database.session() as (c, _):
            profiles = [{**json.loads(r["body"]), "version": r["version"], "activation": r["activation"]}
                        for r in c.execute("SELECT * FROM evidence_profiles ORDER BY sentra_id")]
            runs = [{"id": r["id"], "status": r["status"], "request": json.loads(r["request_json"]),
                     "result": json.loads(r["result_json"]), "started_at": r["started_at"]}
                    for r in c.execute("SELECT * FROM evidence_runs ORDER BY started_at DESC LIMIT 50")]
        return {"profiles": profiles, "runs": runs, "max_profiles": MAX_PROFILES}
