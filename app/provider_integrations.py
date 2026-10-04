"""Demand-first external property provider integration.

Live provider calls are explicit, bounded, cost-capped and stage candidates for
owner review. No provider result creates a deal, sends outreach, or becomes
underwriting truth automatically.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
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
        "max_results_per_request": 50,
        "rights_note": (
            "Use is subject to the connected RentCast account and current terms. "
            "ClubSP stages returned records for review and preserves provider provenance."
        ),
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


def compile_rentcast_search(intent, max_results=25):
    city, state = _market_parts(intent["market"])
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 50:\n        raise ValueError("max_results must be an integer from 1 to 50")
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
    return params


def normalize_rentcast_listing(record):
    if not isinstance(record, dict):
        raise ValueError("RentCast listing must be an object")
    address = str(record.get("addressLine1") or "").strip()
    city = str(record.get("city") or "").strip()
    state = str(record.get("state") or "").strip().upper()
    zip_code = str(record.get("zipCode") or "").strip()
    listing_id = str(record.get("id") or "").strip()
    if not address or not city or not re.fullmatch(r"[A-Z]{2}", state) or not zip_code or not listing_id:
        raise ValueError("RentCast listing is missing required identity fields")
    normalized = {
        "address": address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "parcel_id": str(record.get("assessorID") or listing_id).strip(),
        "provider_listing_id": listing_id,
    }
    mapping = {
        "propertyType": "property_type",
        "price": "asking_price",
        "bedrooms": "beds",
        "bathrooms": "baths",
        "squareFootage": "sqft",
        "yearBuilt": "year_built",
        "status": "listing_status",
        "listedDate": "listed_date",
        "lastSeenDate": "last_seen_date",
        "daysOnMarket": "days_on_market",
    }
    for source, target in mapping.items():
        value = record.get(source)
        if value is not None and value != "":
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

    def _provider_state(self, connection):
        result = []
        for provider_id, provider in PROVIDERS.items():
            usage = self._provider_usage(connection, provider_id)
            cap = self._provider_cap(provider)
            result.append({
                **provider,
                "configured": bool(os.environ.get(provider["credential_env"], "").strip()),
                "monthly_request_cap": cap,
                "remaining_local_requests": max(0, cap - usage["attempted_requests"]),
                "usage": usage,
                "automatic_search": False,
                "requires_explicit_confirmation": True,
            })
        return {"providers": result, "creates_deals": False, "automatic_search": False}

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
        provider = PROVIDERS.get(provider_id)
        if provider is None:
            raise ValueError("unsupported property provider")
        intent_id = text_field(data, "search_intent_id", 500)
        if data.get("confirm_paid_request") is not True:
            raise ValueError("confirm_paid_request must be true for each external provider request")
        max_results_value = data.get("max_results", provider["max_results_per_request"])
        if isinstance(max_results_value, bool) or not isinstance(max_results_value, (int, float)):
            raise ValueError("max_results must be a number")
        max_results = int(max_results_value)
        if max_results != max_results_value or not 1 <= max_results <= provider["max_results_per_request"]:
            raise ValueError(f"max_results must be an integer from 1 to {provider['max_results_per_request']}")

        intents = {item["intent_id"]: item for item in self.search_intents()}
        intent = intents.get(intent_id)
        if intent is None:
            raise LookupError("Current search intent not found; refresh standing buyer mandates")

        api_key = os.environ.get(provider["credential_env"], "").strip()
        if not api_key:
            raise ValueError(f"{provider['credential_env']} is not configured")

        params = compile_rentcast_search(intent, max_results=max_results)
        now = utc_now().isoformat()
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
                (run_id, provider_id, intent_id, json.dumps(params, sort_keys=True), "running", 0, "", "", "", now),
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

    def _stage_provider_candidates(self, *, provider, intent, run_id, normalized, source_url, response_hash, retrieved_at):
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
