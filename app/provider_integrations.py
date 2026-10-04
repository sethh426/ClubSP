"""Demand-first external property provider integration.

Live provider calls are explicit, bounded, cost-capped and stage candidates for
owner review. No provider result creates a deal, sends outreach, or becomes
underwriting truth automatically.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
import json
import os
import re
from urllib.parse import urlencode
from uuid import uuid4

from core.memory.models import utc_now
from .validation import number_field, text_field


PROVIDERS = {
    "rentcast": {
        "id": "rentcast",
        "name": "RentCast",
        "endpoint": "https://api.rentcast.io/v1/listings/sale",
        "credential_env": "RENTCAST_API_KEY",
        "monthly_cap_env": "CLUBSP_RENTCAST_MONTHLY_REQUEST_CAP",
        "default_monthly_cap": 40,
        "cache_hours": 6,
        "max_results_per_request": 50,
        "rights_note": (
            "Use is subject to the connected RentCast account and current terms. "
            "ClubSP stages returned records for review and preserves provider provenance."
        ),
        "capabilities": ["market","property_type","max_price","beds","baths","sqft","year_built","active_listing"],
        "limits": (
            "Listing data is discovery evidence only. It does not prove title, seller authority, "
            "property condition, repair cost, financing, or permission to contact a person."
        ),
    },
}

PROPERTY_TYPES = {
    "single_family": "Single Family",
    "single family": "Single Family",
    "condo": "Condo",
    "townhouse": "Townhouse",
    "manufactured": "Manufactured",
    "multi_family": "Multi-Family",
    "multi-family": "Multi-Family",
    "apartment": "Apartment",
    "land": "Land",
}


def _market_parts(market):
    if not isinstance(market, str):
        raise ValueError("search intent market must be text")
    parts = [part.strip() for part in market.split(",")]
    if len(parts) != 2 or not parts[0] or not re.fullmatch(r"[A-Za-z]{2}", parts[1]):
        raise ValueError("provider search currently requires market formatted as 'City, ST'")
    return parts[0], parts[1].upper()


def compile_rentcast_search(intent, max_results=50):
    city, state = _market_parts(intent["market"])
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 50:
        raise ValueError("max_results must be an integer from 1 to 50")
    params = {
        "city": city,
        "state": state,
        "status": "Active",
        "limit": str(max_results),
        "offset": "0",
    }
    max_price = intent.get("max_total_price")
    if isinstance(max_price, (int, float)) and not isinstance(max_price, bool) and max_price >= 0:
        params["price"] = f"*:{int(max_price)}"
    types = []
    for value in intent.get("property_types") or []:
        mapped = PROPERTY_TYPES.get(str(value).strip().lower().replace("-", "_"))
        if mapped and mapped not in types:
            types.append(mapped)
    if types:
        params["propertyType"] = "|".join(types)
    filters = intent.get("filters") or {}
    ranges = {
        "bedrooms": (filters.get("min_beds"), filters.get("max_beds")),
        "bathrooms": (filters.get("min_baths"), filters.get("max_baths")),
        "squareFootage": (filters.get("min_sqft"), filters.get("max_sqft")),
        "yearBuilt": (filters.get("min_year_built"), filters.get("max_year_built")),
    }
    for key, (low, high) in ranges.items():
        if low is None and high is None:
            continue
        low_text = "*" if low is None else str(int(low) if float(low).is_integer() else low)
        high_text = "*" if high is None else str(int(high) if float(high).is_integer() else high)
        params[key] = f"{low_text}:{high_text}"
    return params


def normalize_rentcast_listing(record):
    if not isinstance(record, dict):
        raise ValueError("RentCast listing must be an object")
    address = str(record.get("addressLine1") or "").strip()
    city = str(record.get("city") or "").strip()
    state = str(record.get("state") or "").strip().upper()
    zip_code = str(record.get("zipCode") or "").strip()
    listing_id = str(record.get("id") or "").strip()
    if not address or len(address) > 300 or not city or len(city) > 120:
        raise ValueError("RentCast listing has invalid address identity")
    if not re.fullmatch(r"[A-Z]{2}", state) or not re.fullmatch(r"\d{5}(?:-\d{4})?", zip_code):
        raise ValueError("RentCast listing has invalid state or ZIP")
    if not listing_id or len(listing_id) > 200:
        raise ValueError("RentCast listing is missing a bounded provider ID")
    parcel = str(record.get("assessorID") or listing_id).strip()
    if not parcel or len(parcel) > 200:
        raise ValueError("RentCast listing has invalid parcel/provider identity")
    normalized = {
        "address": address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "parcel_id": parcel,
        "provider_listing_id": listing_id,
    }
    text_mapping = {
        "propertyType": "property_type",
        "status": "listing_status",
        "listedDate": "listed_date",
        "lastSeenDate": "last_seen_date",
    }
    for source, target in text_mapping.items():
        value = record.get(source)
        if value is None or value == "":
            continue
        value = str(value).strip()
        if len(value) > 200:
            raise ValueError(f"RentCast {source} exceeds supported size")
        normalized[target] = value
    numeric_mapping = {
        "price": ("asking_price", 1_000_000_000),
        "bedrooms": ("beds", 100),
        "bathrooms": ("baths", 100),
        "squareFootage": ("sqft", 10_000_000),
        "yearBuilt": ("year_built", datetime.now(timezone.utc).year + 2),
        "daysOnMarket": ("days_on_market", 100_000),
    }
    for source, (target, maximum) in numeric_mapping.items():
        value = record.get(source)
        if value is None or value == "":
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError(f"RentCast {source} must be a finite number")
        if value < 0 or value > maximum:
            raise ValueError(f"RentCast {source} is outside supported bounds")
        normalized[target] = value
    return normalized


def _live_rentcast_fetch(provider, params, api_key):
    try:
        import httpx
    except ImportError as exc:
        raise ValueError("Live provider access requires: pip install .[integrations]") from exc
    timeout = httpx.Timeout(10.0, connect=5.0, read=10.0, write=10.0, pool=5.0)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.get(
            provider["endpoint"],
            params=params,
            headers={"Accept": "application/json", "X-Api-Key": api_key, "User-Agent": "ClubSP/0.1 demand-first research"},
        )
    if len(response.content) > 2_000_000:
        raise ValueError("Provider response exceeded the 2 MB safety limit")
    if response.status_code == 429:
        raise ValueError("Provider rate limit reached; wait before another explicit search")
    if response.status_code != 200:
        raise ValueError(f"Provider request failed with HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Provider schema changed; expected a listing array")
    return payload, str(response.url)


class ProviderIntegrationMixin:
    def route_property_provider(self, intent, connection=None):
        required = {"market", "max_price"}
        if intent.get("property_types"):
            required.add("property_type")
        filters = intent.get("filters") or {}
        if any(key in filters for key in ("min_beds","max_beds")):
            required.add("beds")
        if any(key in filters for key in ("min_baths","max_baths")):
            required.add("baths")
        if any(key in filters for key in ("min_sqft","max_sqft")):
            required.add("sqft")
        if any(key in filters for key in ("min_year_built","max_year_built")):
            required.add("year_built")
        candidates = []
        owns_connection = connection is None
        if owns_connection:
            ctx = self.database.session()
            connection, _ = ctx.__enter__()
        try:
            for provider_id, provider in PROVIDERS.items():
                supported = set(provider.get("capabilities", []))
                missing = sorted(required - supported)
                usage = self._provider_usage(connection, provider_id)
                cap = self._provider_cap(provider)
                configured = bool(os.environ.get(provider["credential_env"], "").strip())
                remaining = max(0, cap - usage["attempted_requests"])
                candidates.append({
                    "provider_id": provider_id,
                    "name": provider["name"],
                    "configured": configured,
                    "missing_capabilities": missing,
                    "remaining_local_requests": remaining,
                    "eligible": configured and not missing and remaining > 0,
                })
        finally:
            if owns_connection:
                ctx.__exit__(None, None, None)
        candidates.sort(key=lambda item: (not item["eligible"], len(item["missing_capabilities"]), -item["remaining_local_requests"], item["provider_id"]))
        selected = next((item for item in candidates if item["eligible"]), None)
        return {"required_capabilities": sorted(required), "selected_provider_id": selected["provider_id"] if selected else None, "candidates": candidates}

    def _provider_usage(self, connection, provider_id):
        month = utc_now().strftime("%Y-%m")
        attempted = connection.execute(
            "SELECT COUNT(*) FROM provider_search_runs WHERE provider_id=? AND substr(created_at,1,7)=?",
            (provider_id, month),
        ).fetchone()[0]
        succeeded = connection.execute(
            "SELECT COUNT(*) FROM provider_search_runs WHERE provider_id=? AND status='success' AND substr(created_at,1,7)=?",
            (provider_id, month),
        ).fetchone()[0]
        failed = connection.execute(
            "SELECT COUNT(*) FROM provider_search_runs WHERE provider_id=? AND status='failed' AND substr(created_at,1,7)=?",
            (provider_id, month),
        ).fetchone()[0]
        result_total = connection.execute(
            "SELECT COALESCE(SUM(result_count),0) FROM provider_search_runs WHERE provider_id=? AND status='success' AND substr(created_at,1,7)=?",
            (provider_id, month),
        ).fetchone()[0]
        return {
            "month": month,
            "attempted_requests": attempted,
            "successful_requests": succeeded,
            "failed_requests": failed,
            "staged_results": int(result_total or 0),
            "results_per_successful_request": (result_total / succeeded) if succeeded else None,
        }

    def _provider_review_metrics(self, connection, provider_id):
        run_ids = {row["id"] for row in connection.execute(
            "SELECT id FROM provider_search_runs WHERE provider_id=?", (provider_id,)
        )}
        if not run_ids:
            return {"reviewed_candidates": 0, "accepted_candidates": 0, "excluded_candidates": 0, "acceptance_rate": None}
        reviewed = accepted = excluded = 0
        for row in connection.execute("SELECT status,body FROM sourcing_rows WHERE status IN ('accepted','excluded')"):
            body = json.loads(row["body"])
            if body.get("provider_search_run_id") not in run_ids:
                continue
            reviewed += 1
            if row["status"] == "accepted":
                accepted += 1
            elif row["status"] == "excluded":
                excluded += 1
        return {
            "reviewed_candidates": reviewed,
            "accepted_candidates": accepted,
            "excluded_candidates": excluded,
            "acceptance_rate": (accepted / reviewed) if reviewed else None,
        }

    def _provider_state(self, connection):
        result = []
        for provider_id, provider in PROVIDERS.items():
            usage = self._provider_usage(connection, provider_id)
            review_metrics = self._provider_review_metrics(connection, provider_id)
            cap = self._provider_cap(provider)
            result.append({
                **provider,
                "configured": bool(os.environ.get(provider["credential_env"], "").strip()),
                "monthly_request_cap": cap,
                "remaining_local_requests": max(0, cap - usage["attempted_requests"]),
                "usage": usage,
                "review_metrics": review_metrics,
                "automatic_search": False,
                "requires_explicit_confirmation": True,
            })
        intents = self.search_intents()
        routing = [{
            "search_intent_id": intent["intent_id"],
            **self.route_property_provider(intent, connection=connection),
        } for intent in intents]
        return {"providers": result, "routing": routing, "creates_deals": False, "automatic_search": False}

    @staticmethod
    def _provider_cap(provider):
        raw = os.environ.get(provider["monthly_cap_env"], "").strip()
        if not raw:
            return provider["default_monthly_cap"]
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{provider['monthly_cap_env']} must be an integer") from exc
        if not 0 <= value <= 100000:
            raise ValueError(f"{provider['monthly_cap_env']} must be between 0 and 100000")
        return value

    def search_property_provider(self, data):
        provider_id = text_field(data, "provider_id", 40)
        intent_id = text_field(data, "search_intent_id", 500)
        if data.get("confirm_paid_request") is not True:
            raise ValueError("confirm_paid_request must be true for each external provider request")
        intents = {item["intent_id"]: item for item in self.search_intents()}
        intent = intents.get(intent_id)
        if intent is None:
            raise LookupError("Current search intent not found; refresh standing buyer mandates")
        if provider_id == "auto":
            route = self.route_property_provider(intent)
            provider_id = route["selected_provider_id"]
            if not provider_id:
                raise ValueError("No configured provider can satisfy this search intent within the local request budget")
        provider = PROVIDERS.get(provider_id)
        if provider is None:
            raise ValueError("unsupported property provider")

        max_results_value = data.get("max_results", provider["max_results_per_request"])
        if isinstance(max_results_value, bool) or not isinstance(max_results_value, (int, float)):
            raise ValueError("max_results must be a number")
        max_results = int(max_results_value)
        if max_results != max_results_value or not 1 <= max_results <= provider["max_results_per_request"]:
            raise ValueError(f"max_results must be an integer from 1 to {provider['max_results_per_request']}")

        route = self.route_property_provider(intent)
        selected = next((item for item in route["candidates"] if item["provider_id"] == provider_id), None)
        if selected is None or selected["missing_capabilities"]:
            raise ValueError("Selected provider cannot satisfy all required search capabilities")

        api_key = os.environ.get(provider["credential_env"], "").strip()
        if not api_key:
            raise ValueError(f"{provider['credential_env']} is not configured")

        params = compile_rentcast_search(intent, max_results=max_results)
        equivalent_intents = []
        for other in intents.values():
            try:
                if compile_rentcast_search(other, max_results=max_results) == params:
                    equivalent_intents.append(other)
            except ValueError:
                continue
        request_json = json.dumps(params, sort_keys=True)
        now_dt = utc_now()
        now = now_dt.isoformat()
        force_refresh = data.get("force_refresh") is True
        cache_hit = None
        if not force_refresh:
            with self.database.session() as (connection, _):
                prior = connection.execute(
                    """SELECT * FROM provider_search_runs
                       WHERE provider_id=? AND request_json=? AND status='success'
                       ORDER BY created_at DESC,id LIMIT 1""",
                    (provider_id, request_json),
                ).fetchone()
                if prior is not None:
                    age_hours = (now_dt - datetime.fromisoformat(prior["created_at"])).total_seconds() / 3600
                    if 0 <= age_hours < provider.get("cache_hours", 0):
                        batch_id = None
                        for batch_row in connection.execute("SELECT id,body FROM sourcing_batches ORDER BY rowid DESC"):
                            body = json.loads(batch_row["body"])
                            if body.get("provider_search_run_id") == prior["id"]:
                                batch_id = batch_row["id"]
                                break
                        cache_hit = {
                            "id": prior["id"],
                            "source_search_intent_id": prior["search_intent_id"],
                            "provider_id": provider_id,
                            "requested_search_intent_id": intent_id,
                            "result_count": prior["result_count"],
                            "batch_id": batch_id,
                            "status": "success",
                            "cached": True,
                            "cache_age_hours": round(age_hours, 2),
                            "creates_deals": False,
                            "requires_candidate_review": True,
                        }
            if cache_hit:
                with self.database.session(write=True) as (connection, _):
                    for linked in equivalent_intents:
                        connection.execute(
                            """INSERT OR IGNORE INTO provider_search_links(
                                run_id,search_intent_id,mandate_id,buyer_id,linked_at
                            ) VALUES(?,?,?,?,?)""",
                            (cache_hit["id"], linked["intent_id"], linked["mandate_id"], linked["buyer_id"], now),
                        )
                cache_hit["linked_intent_count"] = len(equivalent_intents)
                cache_hit["shared_demand"] = len(equivalent_intents) > 1
                return cache_hit
        run_id = str(uuid4())
        with self.database.session(write=True) as (connection, _):
            usage = self._provider_usage(connection, provider_id)
            cap = self._provider_cap(provider)
            if usage["attempted_requests"] >= cap:
                raise ValueError("Local monthly provider request cap reached; no external request was made")
            connection.execute(
                """INSERT INTO provider_search_runs(
                    id,provider_id,search_intent_id,request_json,status,result_count,
                    response_hash,source_url,error_text,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (run_id, provider_id, intent_id, request_json, "running", 0, "", "", "", now),
            )
            for linked in equivalent_intents:
                connection.execute(
                    """INSERT OR IGNORE INTO provider_search_links(
                        run_id,search_intent_id,mandate_id,buyer_id,linked_at
                    ) VALUES(?,?,?,?,?)""",
                    (run_id, linked["intent_id"], linked["mandate_id"], linked["buyer_id"], now),
                )

        fetcher = getattr(self, "_provider_fetch", _live_rentcast_fetch)
        try:
            payload, source_url = fetcher(provider, params, api_key)
            normalized = []
            seen = set()
            for raw in payload:
                item = normalize_rentcast_listing(raw)
                identity = (item["provider_listing_id"], item["address"].casefold(), item["zip"])
                if identity in seen:
                    continue
                seen.add(identity)
                normalized.append((item, raw))
            response_hash = sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
            ).hexdigest()
            batch_id = self._stage_provider_candidates(
                provider=provider,
                intent=intent,
                run_id=run_id,
                normalized=normalized,
                source_url=source_url,
                response_hash=response_hash,
                retrieved_at=now,
                linked_intents=equivalent_intents,
            )
            with self.database.session(write=True) as (connection, _):
                connection.execute(
                    """UPDATE provider_search_runs
                       SET status='success',result_count=?,response_hash=?,source_url=? WHERE id=?""",
                    (len(normalized), response_hash, source_url, run_id),
                )
            return {
                "id": run_id,
                "provider_id": provider_id,
                "search_intent_id": intent_id,
                "result_count": len(normalized),
                "batch_id": batch_id,
                "status": "success",
                "cached": False,
                "linked_intent_count": len(equivalent_intents),
                "shared_demand": len(equivalent_intents) > 1,
                "creates_deals": False,
                "requires_candidate_review": True,
            }
        except Exception as exc:
            with self.database.session(write=True) as (connection, _):
                connection.execute(
                    "UPDATE provider_search_runs SET status='failed',error_text=? WHERE id=?",
                    (str(exc)[:1000], run_id),
                )
            raise

    def _stage_provider_candidates(self, *, provider, intent, run_id, normalized, source_url, response_hash, retrieved_at, linked_intents=None):
        if not normalized:
            return None
        city, state = _market_parts(intent["market"])
        batch_id = str(uuid4())
        batch_key = sha256(f"{provider['id']}|{intent['intent_id']}|{response_hash}".encode()).hexdigest()
        batch = {
            "id": batch_id,
            "kind": "candidates",
            "provider": provider["name"],
            "source_url": source_url,
            "source_date": retrieved_at[:10],
            "rights_basis": provider["rights_note"],
            "city": city,
            "state": state,
            "raw_hash": response_hash,
            "retrieved_at": retrieved_at,
            "row_count": len(normalized),
            "provider_search_run_id": run_id,
            "search_intent_id": intent["intent_id"],
            "linked_search_intent_ids": [item["intent_id"] for item in (linked_intents or [intent])],
            "linked_mandate_ids": list(dict.fromkeys(item["mandate_id"] for item in (linked_intents or [intent]))),
            "linked_buyer_ids": list(dict.fromkeys(item["buyer_id"] for item in (linked_intents or [intent]))),
        }
        with self.database.session(write=True) as (connection, _):
            prior = connection.execute("SELECT id FROM sourcing_batches WHERE batch_key=?", (batch_key,)).fetchone()
            if prior:
                return prior["id"]
            connection.execute(
                "INSERT INTO sourcing_batches(id,batch_key,body) VALUES(?,?,?)",
                (batch_id, batch_key, json.dumps(batch, allow_nan=False)),
            )
            for line, (item, raw) in enumerate(normalized, 1):
                row_id = str(uuid4())
                value = {
                    "address": item["address"],
                    "zip": item["zip"],
                    "parcel_id": item["parcel_id"],
                    "property_type": item.get("property_type", ""),
                    "city": item["city"],
                    "state": item["state"],
                }
                for key in ("asking_price", "beds", "baths", "sqft", "year_built"):
                    if key in item:
                        value[key] = item[key]
                body = {
                    "id": row_id,
                    "batch_id": batch_id,
                    "line": line,
                    "value": value,
                    "raw": {
                        "provider_listing_id": item["provider_listing_id"],
                        "listing_status": item.get("listing_status"),
                        "listed_date": item.get("listed_date"),
                        "last_seen_date": item.get("last_seen_date"),
                        "days_on_market": item.get("days_on_market"),
                        "response_hash": sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest(),
                    },
                    "errors": [],
                    "provider_search_run_id": run_id,
                }
                connection.execute(
                    "INSERT INTO sourcing_rows(id,batch_id,body,status) VALUES(?,?,?,?)",
                    (row_id, batch_id, json.dumps(body, allow_nan=False), "pending"),
                )
        return batch_id
