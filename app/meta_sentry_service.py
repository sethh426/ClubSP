"""Persistent Meta-Sentra discovery, quarantine, probing and review workflow."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from .meta_sentras import (
    SourceCandidate,
    dedupe_candidates,
    normalize_apify_store_item,
    normalize_ckan_dataset,
    quarantine_record,
)
from .sentra_lifecycle import make_transition
from .schema import assert_component_compatible, ensure_component


META_QUERY_LIMIT = 25
PROBE_BYTES = 262144


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json_hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return sha256(raw).hexdigest()


class MetaSentraMixin:
    def _initialize_meta_sentras(self):
        with self.database.session(write=True) as (connection, _):
            assert_component_compatible(connection, "meta_sentras")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS meta_source_candidates (
                    fingerprint TEXT PRIMARY KEY,
                    discovery_provider TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    description TEXT NOT NULL,
                    jurisdiction_hint TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    assessment_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    schema_fingerprint TEXT NOT NULL DEFAULT '',
                    probe_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS meta_source_events (
                    id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    automated INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS meta_discovery_runs (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    query TEXT NOT NULL,
                    result_count INTEGER NOT NULL,
                    response_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_meta_state ON meta_source_candidates(state,updated_at)")
            ensure_component(connection, "meta_sentras")

    def meta_sentra_state(self):
        with self.database.session() as (connection, _):
            candidates = []
            for row in connection.execute(
                "SELECT * FROM meta_source_candidates ORDER BY updated_at DESC,fingerprint"
            ):
                item = dict(row)
                item["capabilities"] = json.loads(item.pop("capabilities_json"))
                item["metadata"] = json.loads(item.pop("metadata_json"))
                item["assessment"] = json.loads(item.pop("assessment_json"))
                item["probe"] = json.loads(item.pop("probe_json"))
                candidates.append(item)
            runs = [dict(row) for row in connection.execute(
                "SELECT * FROM meta_discovery_runs ORDER BY created_at DESC,id LIMIT 50"
            )]
            return {
                "candidates": candidates,
                "runs": runs,
                "summary": {
                    "total": len(candidates),
                    "quarantined": sum(c["state"] in {"quarantined","metadata_probed","schema_probed"} for c in candidates),
                    "proposed": sum(c["state"] == "proposed" for c in candidates),
                    "approved": sum(c["state"] == "approved" for c in candidates),
                    "active": sum(c["state"] == "active" for c in candidates),
                    "rejected": sum(c["state"] == "rejected" for c in candidates),
                },
                "automatic_activation": False,
            }

    def _store_discovered_candidates(self, provider, query, candidates, response_hash):
        now = _now()
        run_id = str(uuid4())
        stored = 0
        with self.database.session(write=True) as (connection, _):
            for candidate in dedupe_candidates(candidates):
                record = quarantine_record(candidate)
                existing = connection.execute(
                    "SELECT state FROM meta_source_candidates WHERE fingerprint=?",
                    (record["fingerprint"],)
                ).fetchone()
                if existing:
                    connection.execute(
                        """UPDATE meta_source_candidates
                           SET name=?,description=?,jurisdiction_hint=?,capabilities_json=?,
                               metadata_json=?,assessment_json=?,updated_at=?
                           WHERE fingerprint=?""",
                        (
                            record["name"], record["description"], record["jurisdiction_hint"],
                            json.dumps(record["capabilities_hint"], sort_keys=True),
                            json.dumps(record["metadata"], sort_keys=True),
                            json.dumps(record["assessment"], sort_keys=True), now,
                            record["fingerprint"],
                        ),
                    )
                    continue
                connection.execute(
                    """INSERT INTO meta_source_candidates(
                        fingerprint,discovery_provider,external_id,name,source_url,description,
                        jurisdiction_hint,capabilities_json,metadata_json,assessment_json,state,
                        schema_fingerprint,probe_json,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record["fingerprint"], record["discovery_provider"], record["external_id"],
                        record["name"], record["source_url"], record["description"],
                        record["jurisdiction_hint"], json.dumps(record["capabilities_hint"], sort_keys=True),
                        json.dumps(record["metadata"], sort_keys=True),
                        json.dumps(record["assessment"], sort_keys=True), "quarantined",
                        "", "{}", now, now,
                    ),
                )
                transition = make_transition(
                    record["fingerprint"], "discovered", "quarantined",
                    automated=True, reason=f"Discovered by {provider} query: {query}"
                )
                connection.execute(
                    "INSERT INTO meta_source_events VALUES(?,?,?,?,?,?,?,?)",
                    (
                        str(uuid4()), transition.candidate_fingerprint, transition.from_state,
                        transition.to_state, 1, transition.reason,
                        json.dumps(list(transition.evidence_refs)), now,
                    ),
                )
                stored += 1
            connection.execute(
                "INSERT INTO meta_discovery_runs VALUES(?,?,?,?,?,?,?)",
                (run_id, provider, query, len(candidates), response_hash, "success", "", now),
            )
        return {"run_id": run_id, "discovered": len(candidates), "new_quarantined": stored}

    def meta_discover(self, data):
        provider = str(data.get("provider") or "").strip()
        query = str(data.get("query") or "").strip()
        if provider not in {"apify_store", "data_gov", "arcgis_online"}:
            raise ValueError("provider must be apify_store, data_gov, or arcgis_online")
        if not 2 <= len(query) <= 200:
            raise ValueError("query must be 2-200 characters")

        timeout = httpx.Timeout(12.0, connect=5.0)
        candidates = []
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            if provider == "apify_store":
                response = client.get(
                    "https://api.apify.com/v2/store",
                    params={"search": query, "limit": META_QUERY_LIMIT},
                    headers={"Accept":"application/json","User-Agent":"ClubSP/0.1 Meta-Sentra"},
                )
                response.raise_for_status()
                payload = response.json()
                items = payload.get("data", {}).get("items", []) if isinstance(payload, dict) else []
                for item in items[:META_QUERY_LIMIT]:
                    try:
                        candidates.append(normalize_apify_store_item(item))
                    except ValueError:
                        continue
            elif provider == "data_gov":
                response = client.get(
                    "https://catalog.data.gov/api/3/action/package_search",
                    params={"q": query, "rows": META_QUERY_LIMIT},
                    headers={"Accept":"application/json","User-Agent":"ClubSP/0.1 Meta-Sentra"},
                )
                response.raise_for_status()
                payload = response.json()
                items = payload.get("result", {}).get("results", []) if isinstance(payload, dict) else []
                for item in items[:META_QUERY_LIMIT]:
                    try:
                        candidates.append(normalize_ckan_dataset(item, provider="data_gov"))
                    except ValueError:
                        continue
            else:
                response = client.get(
                    "https://www.arcgis.com/sharing/rest/search",
                    params={"q": query + ' AND access:"public"', "num": META_QUERY_LIMIT, "f": "json"},
                    headers={"Accept":"application/json","User-Agent":"ClubSP/0.1 Meta-Sentra"},
                )
                response.raise_for_status()
                payload = response.json()
                items = payload.get("results", []) if isinstance(payload, dict) else []
                for item in items[:META_QUERY_LIMIT]:
                    item_id = str(item.get("id") or "").strip()
                    title = str(item.get("title") or "").strip()
                    if not item_id or not title:
                        continue
                    item_url = str(item.get("url") or "").strip() or (
                        "https://www.arcgis.com/home/item.html?id=" + item_id
                    )
                    description = str(item.get("snippet") or item.get("description") or "").strip()
                    candidates.append(SourceCandidate(
                        discovery_provider="arcgis_hub",
                        external_id=item_id,
                        name=title,
                        source_url=item_url,
                        description=description,
                        jurisdiction_hint=str(item.get("owner") or "").strip(),
                        capabilities_hint=(),
                        metadata={
                            "type": item.get("type"),
                            "owner": item.get("owner"),
                            "modified": item.get("modified"),
                            "tags": item.get("tags", []),
                        },
                    ))
        if len(response.content) > 2_000_000:
            raise ValueError("Meta-Sentra discovery response exceeded limit")
        return self._store_discovered_candidates(
            provider, query, candidates, sha256(response.content).hexdigest()
        )

    def meta_probe(self, data):
        fingerprint = str(data.get("fingerprint") or "").strip()
        if len(fingerprint) != 64:
            raise ValueError("fingerprint is required")
        with self.database.session() as (connection, _):
            row = connection.execute(
                "SELECT * FROM meta_source_candidates WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
        if not row:
            raise LookupError("quarantined source not found")
        if row["state"] not in {"quarantined", "metadata_probed", "requarantined"}:
            raise ValueError("source is not eligible for probing")

        url = row["source_url"]
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            response = client.get(url, headers={"User-Agent":"ClubSP/0.1 Meta-Sentra Probe","Accept":"*/*"})
        sample = response.content[:PROBE_BYTES]
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        fields = []
        shape = "bytes"
        item_count = None
        if "json" in content_type and sample:
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    shape = "object"
                    fields = sorted(str(k)[:120] for k in parsed.keys())[:100]
                    item_count = len(parsed)
                elif isinstance(parsed, list):
                    shape = "array"
                    item_count = len(parsed)
                    keys = set()
                    for item in parsed[:20]:
                        if isinstance(item, dict):
                            keys.update(str(k)[:120] for k in item.keys())
                    fields = sorted(keys)[:100]
            except ValueError:
                shape = "invalid_json"
        schema = {
            "http_status": response.status_code,
            "content_type": content_type,
            "content_length_seen": len(response.content),
            "sample_bytes": len(sample),
            "shape": shape,
            "fields": fields,
            "item_count": item_count,
            "etag": response.headers.get("etag", ""),
            "last_modified": response.headers.get("last-modified", ""),
        }
        schema_fingerprint = _json_hash({
            "content_type": content_type, "shape": shape, "fields": fields
        })
        now = _now()
        with self.database.session(write=True) as (connection, _):
            current = row["state"]
            if current in {"quarantined", "requarantined"}:
                first = make_transition(
                    fingerprint, current, "metadata_probed", automated=True,
                    reason="Bounded metadata probe completed",
                    evidence_refs=(f"http:{response.status_code}",),
                )
                connection.execute(
                    "INSERT INTO meta_source_events VALUES(?,?,?,?,?,?,?,?)",
                    (str(uuid4()), fingerprint, first.from_state, first.to_state, 1,
                     first.reason, json.dumps(list(first.evidence_refs)), now),
                )
                current = "metadata_probed"
            transition = make_transition(
                fingerprint, current, "schema_probed", automated=True,
                reason="Bounded schema probe completed",
                evidence_refs=(f"schema:{schema_fingerprint}",),
            )
            connection.execute(
                """UPDATE meta_source_candidates SET state='schema_probed',schema_fingerprint=?,
                   probe_json=?,updated_at=? WHERE fingerprint=?""",
                (schema_fingerprint, json.dumps(schema, sort_keys=True), now, fingerprint),
            )
            connection.execute(
                "INSERT INTO meta_source_events VALUES(?,?,?,?,?,?,?,?)",
                (str(uuid4()), fingerprint, transition.from_state, transition.to_state, 1,
                 transition.reason, json.dumps(list(transition.evidence_refs)), now),
            )
        return {"fingerprint": fingerprint, "state": "schema_probed", "schema": schema, "schema_fingerprint": schema_fingerprint}

    def meta_propose(self, data):
        fingerprint = str(data.get("fingerprint") or "").strip()
        with self.database.session(write=True) as (connection, _):
            row = connection.execute(
                "SELECT * FROM meta_source_candidates WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            if not row:
                raise LookupError("source candidate not found")
            if row["state"] != "schema_probed":
                raise ValueError("source must be schema_probed before proposal")
            assessment = json.loads(row["assessment_json"])
            if assessment.get("score", 0) < 45:
                raise ValueError("source usefulness score is too low for proposal")
            now = _now()
            transition = make_transition(
                fingerprint, "schema_probed", "proposed", automated=True,
                reason="Schema probe completed and usefulness threshold met",
                evidence_refs=(f"schema:{row['schema_fingerprint']}",),
            )
            connection.execute(
                "UPDATE meta_source_candidates SET state='proposed',updated_at=? WHERE fingerprint=?",
                (now, fingerprint),
            )
            connection.execute(
                "INSERT INTO meta_source_events VALUES(?,?,?,?,?,?,?,?)",
                (str(uuid4()), fingerprint, "schema_probed", "proposed", 1, transition.reason,
                 json.dumps(list(transition.evidence_refs)), now),
            )
        return {"fingerprint": fingerprint, "state": "proposed"}

    def meta_review(self, data):
        fingerprint = str(data.get("fingerprint") or "").strip()
        decision = str(data.get("decision") or "").strip()
        note = str(data.get("note") or "").strip()
        if decision not in {"approve", "reject"} or not note:
            raise ValueError("decision approve/reject and note are required")
        with self.database.session(write=True) as (connection, _):
            row = connection.execute(
                "SELECT * FROM meta_source_candidates WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            if not row:
                raise LookupError("source candidate not found")
            if row["state"] not in {"proposed", "requarantined"}:
                raise ValueError("source is not awaiting review")
            to_state = "approved" if decision == "approve" else "rejected"
            transition = make_transition(
                fingerprint, row["state"], to_state, automated=False, reason=note,
                evidence_refs=(f"schema:{row['schema_fingerprint']}",) if row["schema_fingerprint"] else (),
            )
            now = _now()
            connection.execute(
                "UPDATE meta_source_candidates SET state=?,updated_at=? WHERE fingerprint=?",
                (to_state, now, fingerprint),
            )
            connection.execute(
                "INSERT INTO meta_source_events VALUES(?,?,?,?,?,?,?,?)",
                (str(uuid4()), fingerprint, row["state"], to_state, 0, transition.reason,
                 json.dumps(list(transition.evidence_refs)), now),
            )
        return {"fingerprint": fingerprint, "state": to_state}
