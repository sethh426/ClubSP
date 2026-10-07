"""Persistent Meta-Sentra discovery, review, execution and health workflow."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .meta_sentras import (
    SourceCandidate, dedupe_candidates, infer_capabilities,
    normalize_apify_store_item, normalize_ckan_dataset, normalize_datagov_dataset,
    quarantine_record,
)
from .meta_source_transport import (
    DISCOVERY_BYTES, PROBE_BYTES, fetch_source, source_schema, validate_public_url, arcgis_sample,
)
from .sentra_execution import result_from_payload
from .sentra_lifecycle import make_transition
from .schema import assert_component_compatible, ensure_component
from .sentras import SENTRAS, SentraDefinition


META_QUERY_LIMIT = 25
RUNNABLE_MODES = {"direct_http", "official_api", "arcgis", "file_parser"}
_validate_public_probe_url = validate_public_url


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("fingerprint must be a sha256 hex digest")
    return value


def _bounded_integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return value


def _event(connection, fingerprint, from_state, to_state, *, automated, reason, evidence=()):
    transition = make_transition(fingerprint, from_state, to_state, automated=automated,
                                 reason=reason, evidence_refs=tuple(evidence))
    connection.execute(
        "INSERT INTO meta_source_events VALUES(?,?,?,?,?,?,?,?)",
        (str(uuid4()), fingerprint, from_state, to_state, int(automated),
         transition.reason, json.dumps(list(evidence)), _now()),
    )


def _migrate_meta_v1_to_v2(connection):
    connection.execute("ALTER TABLE activated_sentras ADD COLUMN freshness_target_hours INTEGER NOT NULL DEFAULT 24")
    connection.execute("""
        CREATE TABLE meta_source_checks (
            id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, sentra_id TEXT NOT NULL,
            kind TEXT NOT NULL, status TEXT NOT NULL, response_hash TEXT NOT NULL,
            schema_fingerprint TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL
        )
    """)
    connection.execute("CREATE INDEX idx_meta_checks ON meta_source_checks(fingerprint,created_at)")
    connection.execute("""
        CREATE TABLE meta_scheduler_jobs (
            name TEXT PRIMARY KEY, next_due REAL NOT NULL DEFAULT 0,
            lease_until REAL NOT NULL DEFAULT 0, last_started_at TEXT NOT NULL DEFAULT '',
            last_finished_at TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT ''
        )
    """)


def _compatible_schema(approved, observed):
    if approved.get("shape") != observed.get("shape") or approved.get("fields") != observed.get("fields"):
        return False
    for name, types in observed.get("field_types", {}).items():
        expected = set(approved.get("field_types", {}).get(name, []))
        actual = set(types)
        if "unknown" not in actual and "unknown" not in expected and not actual.issubset(expected):
            return False
    return True


def _decode_candidate(row):
    item = dict(row)
    for stored, public in (("capabilities_json", "capabilities"), ("metadata_json", "metadata"),
                           ("assessment_json", "assessment"), ("probe_json", "probe")):
        item[public] = json.loads(item.pop(stored))
    return item


class MetaSentraMixin:
    def _initialize_meta_sentras(self):
        with self.database.session(write=True) as (connection, _):
            version = assert_component_compatible(connection, "meta_sentras")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS meta_source_candidates (
                    fingerprint TEXT PRIMARY KEY, discovery_provider TEXT NOT NULL,
                    external_id TEXT NOT NULL, name TEXT NOT NULL, source_url TEXT NOT NULL,
                    description TEXT NOT NULL, jurisdiction_hint TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    assessment_json TEXT NOT NULL, state TEXT NOT NULL,
                    schema_fingerprint TEXT NOT NULL DEFAULT '', probe_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS meta_source_events (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL, automated INTEGER NOT NULL, reason TEXT NOT NULL,
                    evidence_json TEXT NOT NULL, created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS meta_discovery_runs (
                    id TEXT PRIMARY KEY, provider TEXT NOT NULL, query TEXT NOT NULL,
                    result_count INTEGER NOT NULL, response_hash TEXT NOT NULL,
                    status TEXT NOT NULL, error_text TEXT NOT NULL, created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS activated_sentras (
                    id TEXT PRIMARY KEY, candidate_fingerprint TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL, family TEXT NOT NULL, acquisition_mode TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL, jurisdiction TEXT NOT NULL,
                    source_url TEXT NOT NULL, rights_note TEXT NOT NULL, activated_at TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_meta_state ON meta_source_candidates(state,updated_at)")
            if version == 0:
                ensure_component(connection, "meta_sentras", target=1)
            if version < 2:
                ensure_component(connection, "meta_sentras", migrations={1: _migrate_meta_v1_to_v2})
            # Legacy activations never had an executable, approved schema contract.
            for row in connection.execute("""
                SELECT a.*,c.probe_json FROM activated_sentras a
                JOIN meta_source_candidates c ON c.fingerprint=a.candidate_fingerprint
            """).fetchall():
                probe = json.loads(row["probe_json"])
                if (row["acquisition_mode"] not in RUNNABLE_MODES or not probe.get("field_types")
                        or row["id"] in SENTRAS):
                    self._requarantine(connection, row["candidate_fingerprint"],
                                       "Activation needs a supported executor and a fresh schema review")

    def meta_sentra_state(self, *, limit=100, offset=0):
        _bounded_integer(limit, "limit", 1, 200)
        _bounded_integer(offset, "offset", 0, 1_000_000)
        with self.database.session() as (connection, _):
            counts = {row["state"]: row["n"] for row in connection.execute(
                "SELECT state,COUNT(*) AS n FROM meta_source_candidates GROUP BY state"
            )}
            candidates = [_decode_candidate(row) for row in connection.execute(
                "SELECT * FROM meta_source_candidates ORDER BY updated_at DESC,fingerprint LIMIT ? OFFSET ?",
                (limit, offset),
            )]
            registry = []
            for row in connection.execute("""
                SELECT a.*,MAX(h.created_at) AS last_checked_at
                FROM activated_sentras a LEFT JOIN meta_source_checks h
                  ON h.fingerprint=a.candidate_fingerprint AND h.created_at>=a.activated_at
                GROUP BY a.id ORDER BY a.id LIMIT ? OFFSET ?
            """, (limit, offset)):
                item = dict(row)
                item["capabilities"] = json.loads(item.pop("capabilities_json"))
                registry.append(item)
            return {
                "candidates": candidates,
                "runs": [dict(row) for row in connection.execute(
                    "SELECT * FROM meta_discovery_runs ORDER BY created_at DESC,id LIMIT 50"
                )],
                "summary": {
                    "total": sum(counts.values()),
                    "quarantined": sum(counts.get(s, 0) for s in ("quarantined", "metadata_probed", "schema_probed")),
                    **{s: counts.get(s, 0) for s in ("proposed", "approved", "active", "rejected", "requarantined")},
                },
                "active_registry": registry,
                "pagination": {"limit": limit, "offset": offset, "has_more": sum(counts.values()) > offset + limit},
                "supported_execution_modes": sorted(RUNNABLE_MODES),
                "provider_readiness": {
                    "data_gov": "configured" if os.environ.get("DATAGOV_API_KEY", "").strip() else "api_key_required",
                    "arcgis_online": "public_catalog", "apify_store": "discovery_only",
                    "ckan": "explicit_catalog_url_required",
                },
                "automatic_activation": False,
                "scheduler_jobs": [dict(row) for row in connection.execute("SELECT * FROM meta_scheduler_jobs ORDER BY name")],
            }

    def meta_candidate_history(self, data):
        fingerprint = _fingerprint(data.get("fingerprint"))
        with self.database.session() as (connection, _):
            if not connection.execute("SELECT 1 FROM meta_source_candidates WHERE fingerprint=?", (fingerprint,)).fetchone():
                raise LookupError("source candidate not found")
            events = []
            for row in connection.execute(
                "SELECT * FROM meta_source_events WHERE fingerprint=? ORDER BY created_at,id", (fingerprint,)
            ):
                item = dict(row)
                item["evidence_refs"] = json.loads(item.pop("evidence_json"))
                events.append(item)
            checks = []
            for row in connection.execute(
                "SELECT * FROM meta_source_checks WHERE fingerprint=? ORDER BY created_at DESC,id LIMIT 100", (fingerprint,)
            ):
                item = dict(row)
                item["details"] = json.loads(item.pop("details_json"))
                checks.append(item)
        return {"fingerprint": fingerprint, "events": events, "checks": checks}

    def _store_discovered_candidates(self, provider, query, candidates, response_hash):
        run_id, stored = str(uuid4()), 0
        with self.database.session(write=True) as (connection, _):
            for candidate in dedupe_candidates(candidates):
                # Fragments do not identify a different data endpoint.
                candidate = replace(candidate, source_url=str(httpx.URL(candidate.source_url).copy_with(fragment=None)))
                record = quarantine_record(candidate)
                existing = connection.execute(
                    "SELECT * FROM meta_source_candidates WHERE fingerprint=? OR source_url=?",
                    (record["fingerprint"], record["source_url"]),
                ).fetchone()
                if existing:
                    # Rediscovery cannot change the reviewed capabilities or approval state.
                    connection.execute("""
                        UPDATE meta_source_candidates SET name=?,description=?,metadata_json=?,updated_at=?
                        WHERE fingerprint=?
                    """, (record["name"], record["description"], json.dumps(record["metadata"], sort_keys=True),
                          _now(), existing["fingerprint"]))
                    continue
                now = _now()
                connection.execute("""
                    INSERT INTO meta_source_candidates(
                        fingerprint,discovery_provider,external_id,name,source_url,description,
                        jurisdiction_hint,capabilities_json,metadata_json,assessment_json,state,
                        schema_fingerprint,probe_json,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    record["fingerprint"], record["discovery_provider"], record["external_id"],
                    record["name"], record["source_url"], record["description"], record["jurisdiction_hint"],
                    json.dumps(record["capabilities_hint"]), json.dumps(record["metadata"], sort_keys=True),
                    json.dumps(record["assessment"], sort_keys=True), "quarantined", "", "{}", now, now,
                ))
                _event(connection, record["fingerprint"], "discovered", "quarantined",
                       automated=True, reason=f"Discovered by {provider} query: {query}")
                stored += 1
            connection.execute(
                "INSERT INTO meta_discovery_runs VALUES(?,?,?,?,?,?,?,?)",
                (run_id, provider, query, len(candidates), response_hash, "success", "", _now()),
            )
        return {"run_id": run_id, "discovered": len(candidates), "new_quarantined": stored}

    def meta_gap_queries(self):
        markets = list(dict.fromkeys(str(i.get("market") or "").strip()
                                    for i in self.search_intents() if str(i.get("market") or "").strip()))
        markets = markets or ["Allen County, Indiana"]
        with self.database.session() as (connection, _):
            coverage = [
                (definition.jurisdiction, set(definition.capabilities))
                for definition in SENTRAS.values() if definition.status == "active" and not definition.family.startswith("meta_")
            ] + [
                (row["jurisdiction"], set(json.loads(row["capabilities_json"])))
                for row in connection.execute("SELECT jurisdiction,capabilities_json FROM activated_sentras")
            ]
        gaps = [
            ("parcel assessor GIS", {"parcel_identity", "assessment", "geospatial"}),
            ("sheriff foreclosure auction", {"sheriff_sale", "foreclosure"}),
            ("tax sale delinquent property", {"tax_sale"}),
            ("building permits code enforcement", {"permit", "code_enforcement"}),
            ("property sales recorder deeds", {"sale_event", "recorded_document"}),
        ]
        queries = []
        for market in markets[:5]:
            active = set()
            market_key = re.sub(r"[^a-z0-9]", "", market.lower())
            for jurisdiction, capabilities in coverage:
                # Broad/provider-plan descriptions do not prove a county is covered.
                if re.sub(r"[^a-z0-9]", "", jurisdiction.lower()) == market_key:
                    active.update(capabilities)
            for phrase, capabilities in gaps:
                missing = capabilities - active
                if missing:
                    queries.append({"query": f"{market} {phrase}", "market": market,
                                    "capability_gap": sorted(missing)})
        return queries[:20]

    def _record_meta_discovery_failure(self, provider, query, error):
        with self.database.session(write=True) as (connection, _):
            connection.execute(
                "INSERT INTO meta_discovery_runs VALUES(?,?,?,?,?,?,?,?)",
                (str(uuid4()), provider, query, 0, "", "failed", str(error)[:1000], _now()),
            )

    def meta_discovery_cycle(self, data):
        providers = data.get("providers", ["data_gov", "arcgis_online", "apify_store"])
        if (not isinstance(providers, list) or not 1 <= len(providers) <= 3
                or any(not isinstance(p, str) or p not in {"data_gov", "arcgis_online", "apify_store"} for p in providers)
                or len(set(providers)) != len(providers)):
            raise ValueError("providers must contain 1-3 distinct supported discovery providers")
        max_queries = _bounded_integer(data.get("max_queries", 6), "max_queries", 1, 12)
        plans, results = self.meta_gap_queries()[:max_queries], []
        for plan in plans:
            for provider in providers:
                try:
                    result = self.meta_discover({"provider": provider, "query": plan["query"]})
                    results.append({"provider": provider, "query": plan["query"], "status": "success", **result})
                except ValueError as exc:
                    # meta_discover already records the failure, including manual calls.
                    results.append({"provider": provider, "query": plan["query"], "status": "failed", "error": str(exc)})
        return {"queries": plans, "attempts": len(results),
                "successes": sum(r["status"] == "success" for r in results),
                "failures": sum(r["status"] == "failed" for r in results),
                "results": results, "automatic_activation": False}

    def meta_discover(self, data):
        provider, query = str(data.get("provider") or "").strip(), str(data.get("query") or "").strip()
        if provider not in {"apify_store", "data_gov", "arcgis_online", "ckan"}:
            raise ValueError("provider must be apify_store, data_gov, arcgis_online, or ckan")
        if not 2 <= len(query) <= 200:
            raise ValueError("query must be 2-200 characters")
        cursor = data.get("cursor")
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 2048):
            raise ValueError("cursor must be a bounded string")
        candidates, headers, next_cursor = [], {}, None
        try:
            if provider == "apify_store":
                offset = int(cursor or "0")
                _bounded_integer(offset, "cursor offset", 0, 10000)
                endpoint = "https://api.apify.com/v2/store"
                params = {"search": query, "limit": META_QUERY_LIMIT, "offset": offset}
            elif provider == "data_gov":
                key = os.environ.get("DATAGOV_API_KEY", "").strip()
                if not key or key == "DEMO_KEY":
                    raise ValueError("DATAGOV_API_KEY is required; automated discovery cannot use DEMO_KEY")
                endpoint = "https://api.gsa.gov/technology/datagov/v4/search"
                headers = {"X-Api-Key": key}
                params = {"q": query, "per_page": META_QUERY_LIMIT}
                if cursor:
                    params["after"] = cursor
            elif provider == "ckan":
                base = validate_public_url(data.get("catalog_url"))
                parsed = urlsplit(base)
                if parsed.query or parsed.fragment:
                    raise ValueError("CKAN catalog URL cannot contain a query or fragment")
                endpoint = base.rstrip("/") + "/api/3/action/package_search"
                offset = int(cursor or "0")
                _bounded_integer(offset, "cursor offset", 0, 10000)
                params = {"q": query, "rows": META_QUERY_LIMIT, "start": offset}
            else:
                start = int(cursor or "1")
                _bounded_integer(start, "cursor start", 1, 10000)
                endpoint = "https://www.arcgis.com/sharing/rest/search"
                params = {"q": query + ' AND access:"public"', "num": META_QUERY_LIMIT, "f": "json", "start": start}
            response = fetch_source(endpoint, params=params, headers=headers, max_bytes=DISCOVERY_BYTES)
            if "json" not in response.content_type:
                raise ValueError("discovery catalog did not return JSON")
            payload = response.json()
            if not isinstance(payload, dict) or "error" in payload or payload.get("success") is False:
                raise ValueError("discovery catalog returned an invalid response")
            if provider == "apify_store":
                result = payload.get("data")
                if not isinstance(result, dict) or not isinstance(result.get("items"), list):
                    raise ValueError("Apify Store response does not match its catalog contract")
                items = result["items"]
                if offset + len(items) < result.get("total", offset + len(items)):
                    next_cursor = str(offset + len(items))
            elif provider == "data_gov":
                items = payload.get("results")
                next_cursor = payload.get("after")
            elif provider == "ckan":
                result = payload.get("result")
                if not isinstance(result, dict):
                    raise ValueError("CKAN response does not match its catalog contract")
                items = result.get("results")
                if isinstance(items, list) and offset + len(items) < result.get("count", offset + len(items)):
                    next_cursor = str(offset + len(items))
            else:
                items = payload.get("results")
                if isinstance(payload.get("nextStart"), int) and payload["nextStart"] > 0:
                    next_cursor = str(payload["nextStart"])
            if not isinstance(items, list):
                raise ValueError("discovery catalog is missing its result list")
            skipped = 0
            for item in items[:META_QUERY_LIMIT]:
                if not isinstance(item, dict):
                    skipped += 1
                    continue
                try:
                    if provider == "apify_store":
                        candidate = normalize_apify_store_item(item)
                    elif provider == "data_gov":
                        candidate = normalize_datagov_dataset(item)
                    elif provider == "ckan":
                        candidate = normalize_ckan_dataset(item)
                        candidate = replace(candidate, external_id=base.rstrip("/") + "#" + candidate.external_id)
                    else:
                        item_id, title = str(item.get("id") or "").strip(), str(item.get("title") or "").strip()
                        if not item_id or not title:
                            raise ValueError("ArcGIS candidate identity is missing")
                        url = str(item.get("url") or "").strip() or "https://www.arcgis.com/home/item.html?id=" + item_id
                        description = str(item.get("snippet") or item.get("description") or "").strip()
                        candidate = SourceCandidate(
                            discovery_provider="arcgis_hub", external_id=item_id, name=title, source_url=url,
                            description=description, capabilities_hint=infer_capabilities(f"{title} {description}"),
                            metadata={"type": item.get("type"), "owner": item.get("owner"), "modified": item.get("modified"),
                                      "access": item.get("access"), "tags": item.get("tags", [])},
                        )
                    validate_public_url(candidate.source_url)
                    candidates.append(candidate)
                except (ValueError, TypeError):
                    skipped += 1
            result = self._store_discovered_candidates(provider, query, candidates, response.payload_hash)
            return {**result, "skipped": skipped, "next_cursor": next_cursor}
        except (ValueError, TypeError, OverflowError) as exc:
            error = str(exc) if isinstance(exc, ValueError) else "discovery catalog response is invalid"
            self._record_meta_discovery_failure(provider, query, error)
            raise ValueError(error) from exc

    def _candidate(self, fingerprint):
        fingerprint = _fingerprint(fingerprint)
        with self.database.session() as (connection, _):
            row = connection.execute("SELECT * FROM meta_source_candidates WHERE fingerprint=?", (fingerprint,)).fetchone()
        if not row:
            raise LookupError("source candidate not found")
        return dict(row)

    def _sample_candidate(self, row):
        if row["discovery_provider"] == "apify_store":
            raise ValueError("Apify Actors remain discovery-only until a cost-controlled Actor adapter is reviewed")
        url = validate_public_url(row["source_url"])
        # A layer endpoint returns HTML without f=json; pin the approved layer URL.
        if re.search(r"/(?:MapServer|FeatureServer)/?$", urlsplit(url).path, re.I):
            raise ValueError("ArcGIS service roots need a declared layer before schema review")
        params = {"f": "json"} if re.search(r"/(?:MapServer|FeatureServer)/\d+/?$", urlsplit(url).path, re.I) else None
        return source_schema(fetch_source(url, params=params, max_bytes=PROBE_BYTES))

    def _probe_arcgis_service(self, row):
        response = fetch_source(row["source_url"], params={"f": "json"}, max_bytes=PROBE_BYTES)
        payload = response.json()
        if not isinstance(payload, dict) or "error" in payload or not isinstance(payload.get("layers"), list):
            raise ValueError("ArcGIS service metadata is missing its layer list")
        layers = []
        for item in payload["layers"][:25]:
            if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] < 0:
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            layers.append(SourceCandidate(
                discovery_provider=row["discovery_provider"], external_id=row["external_id"] + f":layer:{item['id']}",
                name=row["name"] + ": " + name,
                source_url=row["source_url"].rstrip("/") + f"/{item['id']}",
                description=row["description"], jurisdiction_hint=row["jurisdiction_hint"],
                capabilities_hint=tuple(json.loads(row["capabilities_json"])),
                metadata={"parent_fingerprint": row["fingerprint"], "layer_id": item["id"]},
            ))
        if not layers:
            raise ValueError("ArcGIS service has no usable declared layers")
        with self.database.session(write=True) as (connection, _):
            current = connection.execute("SELECT state,updated_at FROM meta_source_candidates WHERE fingerprint=?",
                                         (row["fingerprint"],)).fetchone()
            if not current or current["state"] != row["state"] or current["updated_at"] != row["updated_at"]:
                raise ValueError("source changed during metadata probe")
            if row["state"] != "metadata_probed":
                _event(connection, row["fingerprint"], row["state"], "metadata_probed", automated=True,
                       reason="Public ArcGIS service layer catalog probed", evidence=(f"payload:{response.payload_hash}",))
            details = {"shape": "arcgis_service", "layer_count": len(layers), "payload_hash": response.payload_hash}
            connection.execute("UPDATE meta_source_candidates SET state='metadata_probed',probe_json=?,updated_at=? WHERE fingerprint=?",
                               (json.dumps(details), _now(), row["fingerprint"]))
            self._store_check(connection, row, "probe", "success", details)
        result = self._store_discovered_candidates("arcgis_layers", row["name"][:200], layers, response.payload_hash)
        with self.database.session() as (connection, _):
            children = [dict(connection.execute("SELECT fingerprint,source_url FROM meta_source_candidates WHERE source_url=?",
                                               (candidate.source_url,)).fetchone()) for candidate in layers]
        return {"fingerprint": row["fingerprint"], "state": "metadata_probed", "layer_candidates": children, **result}

    def _store_check(self, connection, row, kind, status, details):
        connection.execute("INSERT INTO meta_source_checks VALUES(?,?,?,?,?,?,?,?,?)", (
            str(uuid4()), row.get("candidate_fingerprint") or row["fingerprint"], row.get("id", ""),
            kind, status, details.get("payload_hash", ""), details.get("schema_fingerprint", ""),
            json.dumps(details, sort_keys=True), _now(),
        ))

    def meta_probe(self, data):
        row = self._candidate(data.get("fingerprint"))
        if row["state"] not in {"quarantined", "metadata_probed", "requarantined"}:
            raise ValueError("source is not eligible for probing")
        try:
            if re.search(r"/(?:MapServer|FeatureServer)/?$", urlsplit(row["source_url"]).path, re.I):
                return self._probe_arcgis_service(row)
            schema, _ = self._sample_candidate(row)
        except ValueError as exc:
            with self.database.session(write=True) as (connection, _):
                self._store_check(connection, row, "probe", "failed", {"error": str(exc)})
            raise
        with self.database.session(write=True) as (connection, _):
            current = connection.execute("SELECT state,updated_at FROM meta_source_candidates WHERE fingerprint=?",
                                         (row["fingerprint"],)).fetchone()
            if not current or current["state"] != row["state"] or current["updated_at"] != row["updated_at"]:
                raise ValueError("source changed during probe; retry against the current state")
            state = current["state"]
            if state != "metadata_probed":
                _event(connection, row["fingerprint"], state, "metadata_probed", automated=True,
                       reason="Successful bounded public metadata probe", evidence=(f"http:200",))
                state = "metadata_probed"
            _event(connection, row["fingerprint"], state, "schema_probed", automated=True,
                   reason="Successful structured schema probe", evidence=(f"schema:{schema['schema_fingerprint']}",))
            connection.execute("""
                UPDATE meta_source_candidates SET state='schema_probed',schema_fingerprint=?,probe_json=?,updated_at=?
                WHERE fingerprint=?
            """, (schema["schema_fingerprint"], json.dumps(schema, sort_keys=True), _now(), row["fingerprint"]))
            self._store_check(connection, row, "probe", "success", schema)
        return {"fingerprint": row["fingerprint"], "state": "schema_probed",
                "schema": schema, "schema_fingerprint": schema["schema_fingerprint"]}

    @staticmethod
    def _reviewable(row):
        probe, assessment = json.loads(row["probe_json"]), json.loads(row["assessment_json"])
        if not row["schema_fingerprint"] or probe.get("http_status") != 200 or not probe.get("field_types"):
            raise ValueError("a successful structured schema probe is required")
        if assessment.get("score", 0) < 45 or assessment.get("blockers"):
            raise ValueError("source usefulness or access blockers prevent proposal")
        if json.loads(row["metadata_json"]).get("access_level") in {"restricted public", "non-public"}:
            raise ValueError("source access restrictions require a dedicated reviewed adapter")

    def meta_propose(self, data):
        fingerprint = _fingerprint(data.get("fingerprint"))
        with self.database.session(write=True) as (connection, _):
            row = connection.execute("SELECT * FROM meta_source_candidates WHERE fingerprint=?", (fingerprint,)).fetchone()
            if not row:
                raise LookupError("source candidate not found")
            if row["state"] != "schema_probed":
                raise ValueError("source must be schema_probed before proposal")
            self._reviewable(row)
            _event(connection, fingerprint, "schema_probed", "proposed", automated=True,
                   reason="Successful schema probe and usefulness threshold met", evidence=(f"schema:{row['schema_fingerprint']}",))
            connection.execute("UPDATE meta_source_candidates SET state='proposed',updated_at=? WHERE fingerprint=?", (_now(), fingerprint))
        return {"fingerprint": fingerprint, "state": "proposed"}

    def meta_review(self, data):
        fingerprint = _fingerprint(data.get("fingerprint"))
        decision, note = str(data.get("decision") or "").strip(), str(data.get("note") or "").strip()
        if decision not in {"approve", "reject"} or not 1 <= len(note) <= 4000:
            raise ValueError("decision approve/reject and a bounded review note are required")
        with self.database.session(write=True) as (connection, _):
            row = connection.execute("SELECT * FROM meta_source_candidates WHERE fingerprint=?", (fingerprint,)).fetchone()
            if not row:
                raise LookupError("source candidate not found")
            if row["state"] != "proposed" and not (decision == "reject" and row["state"] in {"quarantined", "requarantined", "metadata_probed", "schema_probed"}):
                raise ValueError("source needs a fresh schema probe and proposal before approval")
            if decision == "approve":
                self._reviewable(row)
            state = "approved" if decision == "approve" else "rejected"
            # Holding/rejecting an unapproved quarantine source remains an automated-safe transition.
            automated = row["state"] in {"quarantined", "metadata_probed", "schema_probed"} and decision == "reject"
            _event(connection, fingerprint, row["state"], state, automated=automated, reason=note,
                   evidence=(f"schema:{row['schema_fingerprint']}",) if row["schema_fingerprint"] else ())
            connection.execute("UPDATE meta_source_candidates SET state=?,updated_at=? WHERE fingerprint=?", (state, _now(), fingerprint))
        return {"fingerprint": fingerprint, "state": state}

    def meta_activate(self, data):
        fingerprint = _fingerprint(data.get("fingerprint"))
        sentra_id = str(data.get("sentra_id") or "").strip()
        if not re.fullmatch(r"[a-zA-Z0-9_]{1,100}", sentra_id) or sentra_id in SENTRAS:
            raise ValueError("sentra_id must be a unique registry identifier containing letters, numbers and underscores")
        mode = str(data.get("acquisition_mode") or "").strip()
        if mode not in RUNNABLE_MODES:
            raise ValueError("acquisition_mode needs an implemented executor; supported modes: " + ", ".join(sorted(RUNNABLE_MODES)))
        family, jurisdiction, rights = (str(data.get(k) or "").strip() for k in ("family", "jurisdiction", "rights_note"))
        if not 1 <= len(family) <= 100 or not 1 <= len(jurisdiction) <= 200 or not 1 <= len(rights) <= 4000:
            raise ValueError("family, jurisdiction and rights_note are required and must be bounded")
        freshness = _bounded_integer(data.get("freshness_target_hours", 24), "freshness_target_hours", 1, 720)
        with self.database.session(write=True) as (connection, _):
            row = connection.execute("SELECT * FROM meta_source_candidates WHERE fingerprint=?", (fingerprint,)).fetchone()
            if not row:
                raise LookupError("source candidate not found")
            if row["state"] != "approved":
                raise ValueError("source must be explicitly approved before activation")
            self._reviewable(row)
            probe = json.loads(row["probe_json"])
            if (mode == "arcgis") != (probe.get("shape") == "arcgis_layer"):
                raise ValueError("executor must match the probed ArcGIS layer contract")
            if mode == "official_api" and probe.get("shape") == "csv":
                raise ValueError("CSV sources need the direct_http or file_parser executor")
            if connection.execute("SELECT 1 FROM activated_sentras WHERE id=?", (sentra_id,)).fetchone():
                raise ValueError("sentra_id is already registered")
            now = _now()
            connection.execute("""
                INSERT INTO activated_sentras(
                    id,candidate_fingerprint,name,family,acquisition_mode,capabilities_json,
                    jurisdiction,source_url,rights_note,activated_at,freshness_target_hours
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (sentra_id, fingerprint, row["name"], family, mode, row["capabilities_json"],
                  jurisdiction, row["source_url"], rights, now, freshness))
            _event(connection, fingerprint, "approved", "active", automated=True,
                   reason=f"Approved source activated as {sentra_id}", evidence=(f"schema:{row['schema_fingerprint']}",))
            connection.execute("UPDATE meta_source_candidates SET state='active',updated_at=? WHERE fingerprint=?", (now, fingerprint))
        return {"fingerprint": fingerprint, "state": "active", "sentra_id": sentra_id}

    def _active_source(self, sentra_id):
        with self.database.session() as (connection, _):
            row = connection.execute("""
                SELECT a.*,c.fingerprint,c.state,c.probe_json,c.schema_fingerprint,c.discovery_provider
                FROM activated_sentras a JOIN meta_source_candidates c ON c.fingerprint=a.candidate_fingerprint
                WHERE a.id=? AND c.state='active'
            """, (str(sentra_id or "").strip(),)).fetchone()
        if not row:
            raise LookupError("active Sentra not found")
        return dict(row)

    def _requarantine(self, connection, fingerprint, reason):
        _event(connection, fingerprint, "active", "requarantined", automated=True, reason=reason)
        connection.execute("""
            UPDATE meta_source_candidates SET state='requarantined',schema_fingerprint='',probe_json='{}',updated_at=?
            WHERE fingerprint=?
        """, (_now(), fingerprint))
        connection.execute("DELETE FROM activated_sentras WHERE candidate_fingerprint=?", (fingerprint,))

    def meta_requarantine(self, fingerprint, reason):
        fingerprint, reason = _fingerprint(fingerprint), str(reason or "").strip()
        if not 1 <= len(reason) <= 4000:
            raise ValueError("a bounded reason is required")
        with self.database.session(write=True) as (connection, _):
            row = connection.execute("SELECT state FROM meta_source_candidates WHERE fingerprint=?", (fingerprint,)).fetchone()
            if not row:
                raise LookupError("source candidate not found")
            if row["state"] != "active":
                raise ValueError("only active sources can be re-quarantined")
            self._requarantine(connection, fingerprint, reason)
        return {"fingerprint": fingerprint, "state": "requarantined"}

    def _check_active(self, row, kind, where=None):
        try:
            schema, payload = self._sample_candidate(row)
            if not _compatible_schema(json.loads(row["probe_json"]), schema):
                raise ValueError("source schema drifted from its approved field contract")
            if kind == "execution" and row["acquisition_mode"] == "arcgis":
                response, payload = arcgis_sample(row["source_url"], schema, payload, fetch=fetch_source, where=where or "1=1")
                schema = {**schema, "payload_hash": response.payload_hash, "sample_bytes": len(response.body)}
            error = None
        except ValueError as exc:
            schema, payload, error = {"error": str(exc)}, None, str(exc)
        with self.database.session(write=True) as (connection, _):
            current = connection.execute(
                "SELECT activated_at FROM activated_sentras WHERE id=? AND candidate_fingerprint=?",
                (row["id"], row["fingerprint"]),
            ).fetchone()
            if not current or current["activated_at"] != row["activated_at"]:
                raise ValueError("source activation changed during the check; result was discarded")
            self._store_check(connection, row, kind, "failed" if error else "success", schema)
            if error:
                self._requarantine(connection, row["fingerprint"], error)
        return schema, payload, error

    def meta_execute(self, data):
        if set(data) != {"sentra_id"}:
            raise ValueError("execution accepts only the approved sentra_id")
        row = self._active_source(data.get("sentra_id"))
        return self._execute_source(row)

    def _execute_source(self, row, where=None):
        started = time.time_ns()
        schema, payload, error = self._check_active(row, "execution", where=where)
        if error:
            raise ValueError("source was re-quarantined: " + error)
        definition = SentraDefinition(
            id=row["id"], name=row["name"], family=row["family"], acquisition_mode=row["acquisition_mode"],
            capabilities=tuple(json.loads(row["capabilities_json"])), jurisdiction=row["jurisdiction"],
            status="active", source_url=row["source_url"], freshness_target_hours=row["freshness_target_hours"],
            rights_note=row["rights_note"],
        )
        result = result_from_payload(definition, payload, started_ns=started, max_bytes=PROBE_BYTES,
                                     metadata={"candidate_fingerprint": row["fingerprint"],
                                               "schema_fingerprint": row["schema_fingerprint"], "raw_payload_hash": schema["payload_hash"]})
        output = {**asdict(result), "event_key": result.event_key, "evidence_imported": False}
        if where is None:
            self.temporal_observe_result(row, output)
        return output

    def meta_health_check(self, data):
        max_sources = _bounded_integer(data.get("max_sources", 10), "max_sources", 1, 50)
        with self.database.session() as (connection, _):
            ids = [row["id"] for row in connection.execute("""
                SELECT a.id,MAX(h.created_at) AS last_checked FROM activated_sentras a
                LEFT JOIN meta_source_checks h ON h.fingerprint=a.candidate_fingerprint AND h.created_at>=a.activated_at
                GROUP BY a.id ORDER BY COALESCE(MAX(h.created_at),''),a.id LIMIT ?
            """, (max_sources,))]
        results = []
        for sentra_id in ids:
            try:
                row = self._active_source(sentra_id)
                schema, _, error = self._check_active(row, "health")
                results.append({"sentra_id": sentra_id, "status": "requarantined" if error else "healthy",
                                "schema": schema})
            except (ValueError, LookupError) as exc:
                results.append({"sentra_id": sentra_id, "status": "changed", "error": str(exc)})
        return {"checked": len(results), "healthy": sum(r["status"] == "healthy" for r in results),
                "requarantined": sum(r["status"] == "requarantined" for r in results),
                "results": results, "automatic_activation": False}
