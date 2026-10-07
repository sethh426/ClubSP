"""The first 25 fixed-source collectors. No arbitrary URLs or automatic intake.

Each collector emits a versioned evidence envelope. Page/resource observations
remain distinct from property records, modeled prices and aggregate statistics.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import calendar
import io
import json
from math import isfinite
import os
import re
import time
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from pypdf import PdfReader

from .discovery import AnchorLinks, parse_notice, parse_sheriff_sales
from .provider_http import reject_nonfinite, unique_object
from .provider_integrations import compile_realestateapi_count, compile_rentcast_search
from .providers import FIELDS, ParcelResponse, normalized_address, parcel_key
from .sentras import FIRST_25_SENTRA_IDS, SENTRAS


@dataclass(frozen=True)
class CollectorSpec:
    kind: str
    required_inputs: tuple[str, ...] = ()
    max_records: int = 50
    max_requests: int = 1
    daily_request_cap: int = 20


SOURCE_SPECS = {key: CollectorSpec("page_observation", max_records=1) for key in FIRST_25_SENTRA_IDS}
SOURCE_SPECS.update({
    "allen_county_accdc": CollectorSpec("sale_notice", max_records=1),
    "allen_county_north_campus": CollectorSpec("sale_notice", max_records=1),
    "allen_county_sheriff_sales": CollectorSpec("sheriff_sale", max_requests=4),
    "allen_county_imap_parcel": CollectorSpec("parcel_record", ("parcel_key",), max_records=1),
    "rentcast_sale_listings": CollectorSpec("sale_listing", ("search_intent",)),
    "realestateapi_inventory_preflight": CollectorSpec("inventory_count", ("search_intent",), max_records=1),
    "census_address_geocoder": CollectorSpec("address_match", ("address",), max_records=1),
    "census_county_housing": CollectorSpec("county_housing_context", ("state_fips", "county_fips"), max_records=1),
    "rentcast_property_record": CollectorSpec("provider_property_record", ("address",), max_records=1),
    "rentcast_rental_listings": CollectorSpec("rental_listing", ("search_intent",)),
    "rentcast_value_estimate": CollectorSpec("provider_value_estimate", ("address",), max_records=1),
    "rentcast_rent_estimate": CollectorSpec("provider_rent_estimate", ("address",), max_records=1),
    "rentcast_market_statistics": CollectorSpec("zip_market_context", ("zip_code",), max_records=1),
})


def text_input(data, key, maximum=300):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{key} must be bounded nonempty text")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{key} contains control characters")
    return value.strip()


def number(value, label, *, maximum=1_000_000_000, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} is outside supported bounds")
    return value


def compile_source_request(definition, request):
    """Validate inputs before reserving quota or touching an external source."""
    spec = SOURCE_SPECS[definition.id]
    data = request.input_data
    allowed = set(spec.required_inputs)
    if spec.kind in {"sale_listing", "rental_listing"}:
        allowed.add("max_results")
    if spec.kind == "rental_listing":
        allowed.add("max_monthly_rent")
    if set(data) - allowed:
        raise ValueError("Unsupported collector input field")
    for key in spec.required_inputs:
        if key not in data:
            raise ValueError(f"{key} is required")
    method, params, body = "GET", {}, None
    if spec.kind == "parcel_record":
        key = parcel_key(data["parcel_key"])
        params = {"where": "GISPublished.SDE.Parcel_Poly.PIN='" + key + "'",
                  "outFields": ",".join(FIELDS), "returnGeometry": "false", "f": "json"}
    elif spec.kind in {"sale_listing", "rental_listing", "inventory_count"}:
        intent = data["search_intent"]
        if not isinstance(intent, dict) or set(intent) - {"market", "max_total_price", "property_types", "filters"}:
            raise ValueError("search_intent must contain supported buyer-demand fields")
        if "market" not in intent:
            raise ValueError("search_intent.market is required")
        if "max_total_price" in intent and intent["max_total_price"] is not None:
            number(intent["max_total_price"], "max_total_price")
        types = intent.get("property_types", [])
        if not isinstance(types, list) or len(types) > 10 or any(not isinstance(v, str) or len(v) > 40 for v in types):
            raise ValueError("property_types must be a bounded text list")
        filters = intent.get("filters", {})
        if not isinstance(filters, dict) or set(filters) - {"min_beds", "max_beds", "min_baths", "max_baths", "min_sqft", "max_sqft", "min_year_built", "max_year_built"}:
            raise ValueError("Unsupported search_intent filter")
        for field, value in filters.items():
            if value is not None:
                number(value, field)
        for field in ("beds", "baths", "sqft", "year_built"):
            low, high = filters.get("min_" + field), filters.get("max_" + field)
            if low is not None and high is not None and low > high:
                raise ValueError("Search filter minimum exceeds maximum")
        if spec.kind == "inventory_count":
            method, body = "POST", compile_realestateapi_count(intent)
        else:
            maximum = data.get("max_results", 25)
            params = compile_rentcast_search(intent, maximum)
            if spec.kind == "rental_listing":
                # A buyer's acquisition ceiling is not a monthly rental budget.
                params.pop("price", None)
                if data.get("max_monthly_rent") is not None:
                    params["price"] = "*:" + str(number(data["max_monthly_rent"], "max_monthly_rent", maximum=1_000_000))
    elif spec.kind in {"address_match", "provider_property_record", "provider_value_estimate", "provider_rent_estimate"}:
        address = text_input(data, "address")
        params = {"address": address}
        if spec.kind == "address_match":
            params.update({"benchmark": "Public_AR_Current", "format": "json"})
        elif spec.kind == "provider_property_record":
            params.update({"limit": 2, "offset": 0})  # detect ambiguous exact-address responses
        else:
            params["compCount"] = 5
    elif spec.kind == "county_housing_context":
        state, county = text_input(data, "state_fips", 2), text_input(data, "county_fips", 3)
        if not re.fullmatch(r"\d{2}", state) or not re.fullmatch(r"\d{3}", county):
            raise ValueError("state_fips and county_fips must be exact two/three-digit codes")
        params = {"get": "NAME,B25001_001E,B25001_001M,B25002_003E,B25002_003M,B25064_001E,B25064_001M,B25077_001E,B25077_001M",
                  "for": "county:" + county, "in": "state:" + state}
    elif spec.kind == "zip_market_context":
        zip_code = text_input(data, "zip_code", 5)
        if not re.fullmatch(r"\d{5}", zip_code):
            raise ValueError("zip_code must contain five digits")
        params = {"zipCode": zip_code, "dataType": "All", "historyRange": 1}
    return method, params, body


class SourceHTTP:
    def __init__(self, *, max_bytes, max_requests, transport=None):
        self.max_bytes = max_bytes
        self.max_requests = max_requests
        self.transport = transport
        self.bytes_seen = 0
        self.requests = 0
        self.started = time.monotonic()
        self.evidence = []

    def fetch(self, url, *, method="GET", params=None, body=None, credential=None, accept="application/json"):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("Invalid configured source URL")
        if self.requests >= self.max_requests:
            raise ValueError("Collector request limit exceeded")
        self.requests += 1
        headers = {"Accept": accept, "Accept-Encoding": "identity", "User-Agent": "ClubSP/0.1 Sentra"}
        if credential:
            headers["X-Api-Key"] = credential
        try:
            with httpx.Client(transport=self.transport, trust_env=False,
                              timeout=httpx.Timeout(10, connect=5), follow_redirects=False) as client:
                with client.stream(method, url, params=params, json=body, headers=headers) as response:
                    if response.status_code != 200:
                        raise SourceTransportFailure(f"http_{response.status_code}")
                    media = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
                    expected = {"application/json", "text/json", "application/geo+json"} if accept == "application/json" else {accept}
                    if media not in expected:
                        raise SourceTransportFailure("unexpected_media_type")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        self.bytes_seen += len(chunk)
                        if self.bytes_seen > self.max_bytes:
                            raise SourceTransportFailure("response_size_limit")
                        if time.monotonic() - self.started > 45:
                            raise SourceTransportFailure("total_time_limit")
                    if time.monotonic() - self.started > 45:
                        raise SourceTransportFailure("total_time_limit")
                    raw = bytes(raw)
                    self.evidence.append({"url": str(response.url), "sha256": sha256(raw).hexdigest(), "media_type": media,
                                          "bytes": len(raw), "last_modified": response.headers.get("last-modified", "")[:200]})
                    return raw
        except httpx.HTTPError as exc:
            raise SourceTransportFailure("transport_failure") from exc

    def json(self, url, **kwargs):
        raw = self.fetch(url, **kwargs)
        try:
            return json.loads(raw, parse_constant=reject_nonfinite, object_pairs_hook=unique_object)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Source JSON is malformed") from exc


class SourceTransportFailure(ValueError):
    """Only codes generated by this transport may be persisted in error details."""
    def __init__(self, code):
        self.code = code
        super().__init__("Source transport failure: " + code)


class PublicPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title_parts = [], []
        self.skip, self.in_title = 0, False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip += 1
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1
        if tag == "title":
            self.in_title = False
        if tag in {"p", "div", "li", "h1", "h2", "br", "td"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if not self.skip:
            self.parts.append(data)


def observe_page(definition, html):
    page = PublicPage()
    page.feed(html)
    text = " ".join(" ".join(page.parts).split())
    if not text or len(text) < 40:
        raise ValueError("Source page contains insufficient readable content")
    if any(phrase in text.lower() for phrase in ("verify you are human", "just a moment...", "access denied", "enable javascript and cookies")):
        raise ValueError("Source returned an access challenge")
    anchors = AnchorLinks()
    anchors.feed(html)
    terms = {"property", "properties", "tax", "sale", "permit", "record", "document", "zoning", "agenda", "minutes", "hearing", "imap", "surplus", "hud", "pati", "comps"}
    links, seen = [], set()
    for href, label in anchors.links:
        url = urljoin(definition.source_url, href)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or len(url) > 2000 or url in seen:
            continue
        if not any(term in (label + " " + parsed.path).lower() for term in terms):
            continue
        seen.add(url)
        links.append({"url": url, "label": label[:200], "fetched": False})
    links.sort(key=lambda value: (value["url"], value["label"]))
    return {"title": " ".join(" ".join(page.title_parts).split())[:300],
            "excerpt": text[:2000], "text_sha256": sha256(text.encode()).hexdigest(),
            "links": links[:30], "links_truncated": len(links) > 30,
            "scope": "Page and link observations only; no individual property fact is inferred."}


def normalize_property(item):
    if not isinstance(item, dict):
        raise ValueError("Provider property record must be an object")
    record = {"provider_record_id": text_input(item, "id", 200),
              "address": text_input(item, "addressLine1"), "city": text_input(item, "city", 120),
              "state": text_input(item, "state", 2), "zip": text_input(item, "zipCode", 10)}
    if not re.fullmatch(r"[A-Z]{2}", record["state"]) or not re.fullmatch(r"\d{5}(?:-\d{4})?", record["zip"]):
        raise ValueError("Provider address identity is malformed")
    for field in ("addressLine2", "formattedAddress", "assessorID", "propertyType", "lastSaleDate", "listedDate", "lastSeenDate", "status"):
        if item.get(field) not in (None, ""):
            record[field] = text_input(item, field)
    maxima = {"bedrooms": 100, "bathrooms": 100, "squareFootage": 10_000_000,
              "lotSize": 1_000_000_000, "yearBuilt": datetime.now(timezone.utc).year + 2,
              "lastSalePrice": 1_000_000_000, "daysOnMarket": 100000}
    for field, maximum in maxima.items():
        if item.get(field) is not None:
            record[field] = number(item[field], field, maximum=maximum)
    return record


def require_requested_address(record, address):
    parts = [record["address"], record.get("addressLine2", ""), record["city"], record["state"], record["zip"]]
    if normalized_address(address) != normalized_address(" ".join(parts)):
        raise ValueError("Provider returned a different or unresolved address")


def collect_source(definition, request, *, transport=None):
    spec = SOURCE_SPECS[definition.id]
    method, params, body = compile_source_request(definition, request)
    credential = None
    if definition.credential_env:
        credential = os.environ.get(definition.credential_env, "").strip()
        if not credential:
            raise ValueError(f"{definition.credential_env} is not configured")
    http = SourceHTTP(max_bytes=request.max_bytes, max_requests=spec.max_requests, transport=transport)
    records, notes = [], {}
    now = datetime.now(timezone.utc)
    if spec.kind in {"page_observation", "sale_notice"}:
        html = http.fetch(definition.source_url, accept="text/html").decode("utf-8")
        page = observe_page(definition, html)
        records = [page]
        if spec.kind == "sale_notice":
            notice = parse_notice("accdc" if definition.id == "allen_county_accdc" else "north_campus", html, now)
            page.update({"notice_status": notice["status"], "candidates": notice["candidates"]})
    elif spec.kind == "sheriff_sale":
        html = http.fetch(definition.source_url, accept="text/html").decode("utf-8")
        anchors = AnchorLinks()
        anchors.feed(html)
        local = now.astimezone(ZoneInfo("America/Indiana/Indianapolis"))
        wanted = []
        for offset in range(3):
            month = local.month + offset
            wanted.append(f"{calendar.month_name[(month - 1) % 12 + 1].upper()} {local.year + (month - 1) // 12}")
        documents = {}
        for href, label in anchors.links:
            label = label.strip().upper()
            url = urljoin(definition.source_url, href)
            parsed = urlsplit(url)
            if label not in wanted or not parsed.path.lower().endswith(".pdf"):
                continue
            if parsed.hostname != urlsplit(definition.source_url).hostname or parsed.username or parsed.password or parsed.scheme != "https":
                raise ValueError("Sheriff document link left the configured source")
            documents[label] = url
        if not documents:
            raise ValueError("No current sheriff-sale documents found")
        extracted = []
        for label in wanted:
            if label not in documents:
                continue
            pdf = http.fetch(documents[label], accept="application/pdf")
            reader = PdfReader(io.BytesIO(pdf), strict=False)
            if reader.is_encrypted or not 1 <= len(reader.pages) <= 10:
                raise ValueError("Unsupported sheriff document")
            text = "\n".join(page.extract_text(extraction_mode="layout") or "" for page in reader.pages)
            if len(text) > 200000:
                raise ValueError("Sheriff extracted text exceeded its limit")
            extracted.append(f"[[SOURCE_DOCUMENT:{documents[label]}]]\n{text}")
        parsed = parse_sheriff_sales("\n".join(extracted), now)
        records, notes = parsed["candidates"], {"notice_status": parsed["status"]}
    elif spec.kind == "parcel_record":
        raw = http.json(definition.source_url + "/query", params=params)
        if not isinstance(raw, dict) or raw.get("error"):
            raise ValueError("ArcGIS returned an error or changed its schema")
        parsed = ParcelResponse.model_validate(raw)
        if parsed.exceededTransferLimit or len(parsed.features) > 1:
            raise ValueError("Parcel identity is ambiguous or truncated")
        for feature in parsed.features:
            attrs = feature.attributes
            if parcel_key(str(attrs.get(next(iter(FIELDS)), ""))) != parcel_key(request.input_data["parcel_key"]):
                raise ValueError("ArcGIS returned a different parcel")
            record = {}
            for field, name in FIELDS.items():
                value = attrs.get(field)
                if value in (None, ""):
                    continue
                if name == "reported_transfer_date":
                    value = datetime.fromtimestamp(number(value, name, maximum=now.timestamp() * 1000) / 1000, timezone.utc).date().isoformat()
                elif not isinstance(value, (str, int)) or isinstance(value, bool) or len(str(value)) > 1000:
                    raise ValueError("ArcGIS record field is malformed")
                record[name] = value.strip() if isinstance(value, str) else value
            records.append(record)
    else:
        raw = http.json(definition.source_url, method=method, params=params, body=body, credential=credential)
        if spec.kind in {"sale_listing", "rental_listing", "provider_property_record"}:
            if not isinstance(raw, list) or len(raw) > (2 if spec.kind == "provider_property_record" else int(params["limit"])):
                raise ValueError("Provider record count or schema changed")
            if spec.kind == "provider_property_record" and len(raw) > 1:
                raise ValueError("Provider property identity is ambiguous")
            for item in raw:
                record = normalize_property(item)
                if spec.kind == "provider_property_record":
                    require_requested_address(record, request.input_data["address"])
                    # Preserve selected factual fields; do not persist owner contacts.
                    for field in ("taxAssessments", "propertyTaxes", "history"):
                        value = item.get(field)
                        if value is not None:
                            if not isinstance(value, dict) or len(value) > 100:
                                raise ValueError("Provider history schema changed")
                            permitted = {"taxAssessments": {"year", "value", "land", "improvements"},
                                         "propertyTaxes": {"year", "total"}, "history": {"event", "date", "price"}}[field]
                            selected = {}
                            for event_id, event in value.items():
                                if not isinstance(event_id, str) or len(event_id) > 80 or not isinstance(event, dict):
                                    raise ValueError("Provider history row schema changed")
                                selected[event_id] = {name: (text_input(event, name, 80) if name in {"event", "date"}
                                                            else number(event[name], name)) for name in permitted if event.get(name) is not None}
                            record[field] = selected
                else:
                    city, state = request.input_data["search_intent"]["market"].split(",")
                    if record["city"].casefold() != city.strip().casefold() or record["state"] != state.strip().upper() or item.get("status") != "Active":
                        raise ValueError("Provider listing is outside the requested active market")
                    record["asking_rent" if spec.kind == "rental_listing" else "asking_price"] = number(item.get("price"), "price")
                records.append(record)
        elif spec.kind == "inventory_count":
            if not isinstance(raw, dict):
                raise ValueError("Inventory count schema changed")
            count = raw.get("resultCount")
            if count is None and isinstance(raw.get("data"), dict):
                count = raw["data"].get("resultCount")
            if type(count) is not int or not 0 <= count <= 100_000_000:
                raise ValueError("Inventory response lacks a valid count")
            records = [{"total_count": count, "market": request.input_data["search_intent"]["market"], "mode": "count"}]
        elif spec.kind == "address_match":
            if not isinstance(raw, dict) or not isinstance(raw.get("result"), dict) or not isinstance(raw["result"].get("addressMatches"), list):
                raise ValueError("Geocoder schema changed")
            matches = raw["result"]["addressMatches"]
            if len(matches) > 1:
                raise ValueError("Geocoder address is ambiguous")
            for item in matches:
                coords = item.get("coordinates", {})
                records.append({"requested_address": request.input_data["address"], "matched_address": text_input(item, "matchedAddress"),
                                "longitude": number(coords.get("x"), "longitude", minimum=-180, maximum=180),
                                "latitude": number(coords.get("y"), "latitude", minimum=-90, maximum=90),
                                "coordinate_basis": "Census address range; parcel identity unverified"})
        elif spec.kind == "county_housing_context":
            if not isinstance(raw, list) or len(raw) != 2 or not all(isinstance(row, list) for row in raw) or len(raw[0]) != len(raw[1]):
                raise ValueError("Census county table schema changed")
            record = dict(zip(raw[0], raw[1]))
            if record.get("state") != request.input_data["state_fips"] or record.get("county") != request.input_data["county_fips"]:
                raise ValueError("Census returned a different county")
            expected = params["get"].split(",")
            if set(raw[0]) != set(expected) | {"state", "county"} or len(raw[0]) != len(set(raw[0])):
                raise ValueError("Census housing fields are missing")
            records = [{"survey_year": 2024, "survey_period": "2020-2024", "state_fips": record["state"], "county_fips": record["county"], "name": text_input(record, "NAME"),
                        "values": {field: (int(record[field]) if record[field] is not None and int(record[field]) >= 0 else None) for field in expected[1:]},
                        "scope": "County estimates and margins of error; negative Census sentinel values are missing data."}]
        elif spec.kind in {"provider_value_estimate", "provider_rent_estimate"}:
            if not isinstance(raw, dict) or not isinstance(raw.get("subjectProperty"), dict):
                raise ValueError("Provider estimate subject identity is missing")
            subject = normalize_property(raw["subjectProperty"])
            require_requested_address(subject, request.input_data["address"])
            prefix = "price" if spec.kind == "provider_value_estimate" else "rent"
            estimate, low, high = (number(raw.get(field), field) for field in (prefix, prefix + "RangeLow", prefix + "RangeHigh"))
            if not low <= estimate <= high:
                raise ValueError("Provider estimate range is inconsistent")
            records = [{"subject": subject, "estimate": estimate, "range_low": low, "range_high": high,
                        "basis": "Provider AVM estimate; requires separate underwriting review"}]
        elif spec.kind == "zip_market_context":
            if not isinstance(raw, dict) or raw.get("zipCode") != request.input_data["zip_code"]:
                raise ValueError("Provider market ZIP identity changed")
            record = {"zip_code": raw["zipCode"]}
            for field in ("saleData", "rentalData"):
                data = raw.get(field)
                if data is None:
                    continue
                if not isinstance(data, dict):
                    raise ValueError("Provider market data schema changed")
                subset = {key: number(data[key], key) for key in ("averagePrice", "medianPrice", "averageRent", "medianRent", "averageDaysOnMarket", "totalListings", "newListings") if data.get(key) is not None}
                if data.get("lastUpdatedDate"):
                    subset["lastUpdatedDate"] = text_input(data, "lastUpdatedDate", 80)
                record[field] = subset
            if len(record) == 1:
                notes["coverage_status"] = "no_market_data"
            else:
                records = [record]
    if len(records) > spec.max_records:
        raise ValueError("Normalized source records exceeded the collector limit")
    # Deterministic normalization and per-record keys make repeats comparable.
    unique = {}
    for record in records:
        key = sha256(json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        unique[key] = {"record_key": key, **record, "review_state": "unreviewed"}
    payload = {"schema_version": 1, "kind": spec.kind, "records": [unique[key] for key in sorted(unique)],
               "coverage_status": "observed" if records else "no_match", **notes}
    # A response digest proves what was observed; query identity is separate.
    metadata = {"evidence": http.evidence, "http_requests": http.requests, "bytes_seen": http.bytes_seen,
                "request": {"method": method, "params": params, "body": body},
                "query_sha256": sha256(json.dumps({"method": method, "params": params, "body": body}, sort_keys=True).encode()).hexdigest(),
                "schema_fingerprint": sha256(json.dumps({"version": 1, "kind": spec.kind,
                    "fields": sorted(set().union(*(record.keys() for record in records))) if records else []}, sort_keys=True).encode()).hexdigest(),
                "requires_evidence_review": True, "creates_deals": False}
    return payload, metadata
