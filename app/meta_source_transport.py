"""Bounded, public-only transport and stable schemas for discovered sources.

Catalog metadata and acquired samples stay outside the business evidence store.
Public DNS is resolved once and pinned for each request, including TLS SNI.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from hashlib import sha256
import io
import ipaddress
import json
import socket
import time
from urllib.parse import urlsplit

import httpx

from .provider_http import reject_nonfinite, unique_object


PROBE_BYTES = 262144
DISCOVERY_BYTES = 2_000_000
TOTAL_SECONDS = 15


def validate_public_url(value):
    value = str(value or "").strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("source must be a valid public HTTPS URL") from exc
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or port not in (None, 443)):
        raise ValueError("source must be a public HTTPS URL on port 443")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal")):
        raise ValueError("local source targets are not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError("local source targets are not allowed")
        return value
    if not address.is_global:
        raise ValueError("private, loopback, reserved, and link-local sources are not allowed")
    return value


class PublicHTTPTransport(httpx.BaseTransport):
    def __init__(self):
        self._inner = httpx.HTTPTransport(retries=0)

    def handle_request(self, request):
        validate_public_url(str(request.url))
        host = request.url.host
        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("source DNS lookup failed") from exc
        if not addresses:
            raise ValueError("source DNS lookup returned no addresses")
        ips = [entry[4][0] for entry in addresses]
        if any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError("source DNS resolves to a non-public address")
        pinned = httpx.Request(
            request.method, request.url.copy_with(host=ips[0]),
            headers=request.headers, stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )
        return self._inner.handle_request(pinned)

    def close(self):
        self._inner.close()


@dataclass(frozen=True)
class SourceResponse:
    url: str
    content_type: str
    body: bytes
    headers: dict
    duration_ms: int

    @property
    def payload_hash(self):
        return sha256(self.body).hexdigest()

    def json(self):
        try:
            return json.loads(self.body, parse_constant=reject_nonfinite,
                              object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError("source returned invalid JSON") from exc


def fetch_source(url, *, params=None, headers=None, max_bytes=PROBE_BYTES, transport=None):
    url = validate_public_url(url)
    started = time.monotonic()
    request_headers = {
        "Accept": "application/json,text/csv;q=0.9",
        "Accept-Encoding": "identity",
        "User-Agent": "ClubSP/0.1 Meta-Sentra",
        **(headers or {}),
    }
    try:
        with httpx.Client(
            transport=transport or PublicHTTPTransport(), trust_env=False,
            timeout=httpx.Timeout(10, connect=5), follow_redirects=False,
        ) as client:
            with client.stream("GET", url, params=params, headers=request_headers) as response:
                if response.status_code != 200:
                    raise ValueError(f"source returned HTTP {response.status_code}")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ValueError("source must return an uncompressed bounded sample")
                length = response.headers.get("content-length", "")
                if length.isdigit() and int(length) > max_bytes:
                    raise ValueError("source response exceeded the bounded sample limit")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    if time.monotonic() - started > TOTAL_SECONDS:
                        raise ValueError("source response exceeded the total time limit")
                    if len(body) + len(chunk) > max_bytes:
                        raise ValueError("source response exceeded the bounded sample limit")
                    body.extend(chunk)
                if time.monotonic() - started > TOTAL_SECONDS:
                    raise ValueError("source response exceeded the total time limit")
                return SourceResponse(
                    str(response.url), response.headers.get("content-type", "").split(";", 1)[0].lower().strip(),
                    bytes(body), dict(response.headers), int((time.monotonic() - started) * 1000),
                )
    except httpx.HTTPError as exc:
        # Never persist provider URLs, query credentials, or response bodies in errors.
        raise ValueError("source transport failed") from exc


def _type_name(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"


def source_schema(response):
    """Parse JSON/CSV samples; fingerprint field contracts, never volatile values."""
    content_type = response.content_type
    if content_type in {"text/csv", "application/csv", "application/vnd.ms-excel"}:
        try:
            reader = csv.DictReader(io.StringIO(response.body.decode("utf-8-sig")))
            names = reader.fieldnames or []
            if not names or len(names) > 100 or len(set(names)) != len(names) or any(not name for name in names):
                raise ValueError("CSV field names are missing, duplicate, or excessive")
            rows = list(reader)
            if any(None in row or None in row.values() for row in rows):
                raise ValueError("CSV rows do not match the field contract")
        except (UnicodeError, csv.Error) as exc:
            raise ValueError("source returned invalid CSV") from exc
        payload = rows
        shape = "csv"
        field_types = {name: ["string"] for name in names}
        item_count = len(rows)
    elif content_type.endswith("json") or content_type.endswith("+json"):
        payload = response.json()
        if not isinstance(payload, (dict, list)):
            raise ValueError("JSON source must contain an object or array")
        if isinstance(payload, dict) and ("error" in payload or payload.get("success") is False):
            raise ValueError("source returned a provider error")
        if isinstance(payload, dict) and isinstance(payload.get("fields"), list) and payload.get("fields"):
            # ArcGIS layer metadata carries the actual field/type contract.
            descriptors = payload["fields"]
            if any(not isinstance(f, dict) or not f.get("name") or not f.get("type") for f in descriptors):
                raise ValueError("ArcGIS layer field contract is invalid")
            if len({f["name"] for f in descriptors}) != len(descriptors):
                raise ValueError("ArcGIS layer has duplicate field names")
            field_types = {f["name"]: [f["type"]] for f in descriptors}
            shape = "arcgis_layer"
            item_count = len(descriptors)
        else:
            rows = payload if isinstance(payload, list) else [payload]
            shape = "array" if isinstance(payload, list) else "object"
            # Extract common structured result envelopes without confusing metadata with rows.
            if isinstance(payload, dict):
                for key in ("features", "records", "items", "results"):
                    if isinstance(payload.get(key), list):
                        rows = payload[key]
                        shape = key
                        break
            if not rows or any(not isinstance(row, dict) for row in rows):
                raise ValueError("source sample must contain structured records")
            types = {}
            for row in rows[:25]:
                fields = row.get("attributes") if shape == "features" else row
                if not isinstance(fields, dict):
                    raise ValueError("source record field contract is invalid")
                for key, value in fields.items():
                    if len(str(key)) > 120:
                        raise ValueError("source field name exceeded the limit")
                    if value is not None:
                        types.setdefault(str(key), set()).add(_type_name(value))
                    else:
                        types.setdefault(str(key), set())
            # Nullability/value changes do not falsely count as type drift.
            field_types = {key: sorted(values) or ["unknown"] for key, values in types.items()}
            item_count = len(rows)
    else:
        raise ValueError("source needs a dedicated adapter; only JSON, CSV, and ArcGIS layers are executable")
    if not field_types or len(field_types) > (256 if shape == "arcgis_layer" else 100):
        raise ValueError("source field contract is empty or excessive")
    fields = sorted(field_types)
    contract = {"shape": shape, "fields": fields, "field_types": field_types}
    fingerprint = sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    schema = {
        **contract, "http_status": 200, "content_type": content_type,
        "schema_fingerprint": fingerprint, "sample_bytes": len(response.body),
        "payload_hash": response.payload_hash, "item_count": item_count,
        "duration_ms": response.duration_ms, "etag": response.headers.get("etag", ""),
        "last_modified": response.headers.get("last-modified", ""),
        "identity_fields": [f for f in fields if f.lower() in {"id", "objectid", "parcel_id", "parcelid", "pin", "identifier"}],
        "freshness_fields": [f for f in fields if any(t in f.lower() for t in ("updated", "modified", "timestamp", "date"))],
    }
    return schema, payload


def arcgis_sample(url, schema, metadata, fetch=fetch_source, where="1=1"):
    """At most 25 rows, including legacy layers without pagination.

    Legacy ID inventories are capped at 2 MiB; only 25 selected IDs are
    requested as records. This is a sample, never a county-wide export.
    """
    endpoint = url.rstrip("/") + "/query"
    params = {"f": "json", "where": where, "outFields": "*", "returnGeometry": "false"}
    selected = None
    if metadata.get("advancedQueryCapabilities", {}).get("supportsPagination") is False:
        oid_fields = [f for f, types in schema["field_types"].items() if types == ["esriFieldTypeOID"]]
        if len(oid_fields) != 1:
            raise ValueError("legacy ArcGIS layer needs one object-ID field")
        inventory = fetch(endpoint, params={"f": "json", "where": where, "returnIdsOnly": "true"},
                          max_bytes=DISCOVERY_BYTES).json()
        if (not isinstance(inventory, dict) or "error" in inventory or
                inventory.get("objectIdFieldName") != oid_fields[0] or
                not isinstance(inventory.get("objectIds"), list) or
                len(inventory["objectIds"]) > 250000 or
                any(type(i) is not int or i < 0 for i in inventory["objectIds"])):
            raise ValueError("legacy ArcGIS object-ID inventory is invalid or excessive")
        selected = sorted(set(inventory["objectIds"]))[:25]
        if not selected:
            params["where"] = "1=0"
        else:
            params["objectIds"] = ",".join(map(str, selected))
    else:
        params["resultRecordCount"] = 25
    response = fetch(endpoint, params=params)
    payload = response.json()
    if (not isinstance(payload, dict) or "error" in payload or
            not isinstance(payload.get("features"), list) or len(payload["features"]) > 25):
        raise ValueError("ArcGIS query did not return its bounded record envelope")
    expected = set(schema["fields"])
    geometry = {f for f, types in schema["field_types"].items() if types == ["esriFieldTypeGeometry"]}
    # Legacy joined map layers also omit computed geometry expressions when
    # returnGeometry=false. Ordinary acreage/area fields remain required.
    geometry |= {f for f, types in schema["field_types"].items()
                 if f.lower() in {"shape.starea()", "shape.stlength()"} and types == ["esriFieldTypeDouble"]}
    for feature in payload["features"]:
        if (not isinstance(feature, dict) or not isinstance(feature.get("attributes"), dict) or
                not expected - geometry <= set(feature["attributes"]) <= expected):
            raise ValueError("ArcGIS records drifted from the approved layer fields")
        if selected is not None and feature["attributes"].get(oid_fields[0]) not in selected:
            raise ValueError("legacy ArcGIS returned an unrequested object ID")
    return response, payload
