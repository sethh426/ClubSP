"""Conservative Allen County GIS parcel resolution for sheriff-sale research."""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from .providers import NoRedirect, parcel_key


SITE_ADDRESS_QUERY = "https://gis.acimap.us/acfw/rest/services/Parcels/SiteAddresses_TrimbleUnity/FeatureServer/0/query"
PARCEL_QUERY = "https://gis.acimap.us/acfw/rest/services/Parcels/AC_Parcel_iMap_org/FeatureServer/20/query"
MAX_RESPONSE = 262144


def _json_get(url, params, request=None):
    request = request or _default_request
    return request(url, params)


def _default_request(url, params):
    target = url + "?" + urlencode(params)
    req = Request(target, headers={
        "User-Agent": "ClubSP/0.1 bounded Allen County parcel identity check",
        "Accept": "application/json",
    })
    with build_opener(NoRedirect()).open(req, timeout=10) as response:
        if "json" not in response.headers.get("Content-Type", "").lower():
            raise ValueError("Unexpected Allen County GIS response format")
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError("Allen County GIS response exceeds supported size")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError("Allen County GIS query failed")
    return data


def canonical_address(value):
    value = str(value).upper()
    # Sheriff notices sometimes render unit IDs as B-206 while GIS uses B206.
    value = re.sub(r"\b([A-Z])-(\d{2,4})\b", r"\1\2", value)
    value = re.sub(r"[^A-Z0-9 ]+", " ", value)
    tokens = value.split()
    synonyms = {
        "SAINT": "ST", "STREET": "ST", "AVENUE": "AVE", "BOULEVARD": "BLVD",
        "ROAD": "RD", "DRIVE": "DR", "LANE": "LN", "COURT": "CT",
        "CIRCLE": "CIR", "PARKWAY": "PKWY", "PLACE": "PL", "TERRACE": "TER",
        "APARTMENT": "", "APT": "", "UNIT": "", "SUITE": "", "STE": "",
    }
    tokens = [synonyms.get(token, token) for token in tokens]
    tokens = [token for token in tokens if token]
    directions = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}
    # Allen County data can place the directional before or after the road name.
    if len(tokens) >= 3 and tokens[-1] in directions:
        direction = tokens.pop()
        tokens.insert(1, direction)
    return "".join(tokens)


def _rows(data):
    features = data.get("features", [])
    if not isinstance(features, list):
        raise ValueError("Allen County GIS returned invalid features")
    rows = []
    for feature in features:
        attrs = feature.get("attributes") if isinstance(feature, dict) else None
        if isinstance(attrs, dict):
            rows.append(attrs)
    return rows


def resolve_parcel_identity(candidate, request=None):
    """Resolve only a single exact normalized official site address.

    A sheriff address with a unit suffix will not match a base-building address.
    Multiple distinct parcel matches remain ambiguous.
    """
    address = candidate.get("address", "")
    zip_code = candidate.get("zip", "")
    match = re.match(r"^\s*([0-9]+[A-Z]?)\s+(.+?)\s*$", address, re.I)
    if not match or not re.fullmatch(r"\d{5}", str(zip_code)):
        return {"status": "unresolved", "reason": "Address or ZIP is not resolvable by the bounded matcher"}

    number = match.group(1).upper()
    target = canonical_address(address)
    where = "addrnum='" + number.replace("'", "''") + "' AND ZIP='" + str(zip_code) + "'"
    site_data = _json_get(SITE_ADDRESS_QUERY, {
        "f": "json", "where": where,
        "outFields": "fulladdr,addrnum,unittype,unitid,PIN,GIS_ID,ZIP,municipality",
        "returnGeometry": "false", "resultRecordCount": "200",
    }, request=request)

    matches = {}
    for attrs in _rows(site_data):
        fulladdr = attrs.get("fulladdr")
        pin, gis_id = attrs.get("PIN"), attrs.get("GIS_ID")
        if not fulladdr or not pin or not gis_id:
            continue
        if canonical_address(fulladdr) != target:
            continue
        try:
            parcel_key(str(gis_id).replace(".", ""))
        except ValueError:
            continue
        matches[(str(pin), str(gis_id))] = attrs

    if not matches:
        suggestions = []
        for attrs in _rows(site_data):
            fulladdr = attrs.get("fulladdr")
            pin, gis_id = attrs.get("PIN"), attrs.get("GIS_ID")
            if not fulladdr or not pin or not gis_id:
                continue
            try:
                parcel_key(str(gis_id).replace(".", ""))
            except ValueError:
                continue
            score = SequenceMatcher(None, target, canonical_address(fulladdr)).ratio()
            if score >= 0.80:
                suggestions.append({
                    "official_address": fulladdr, "pin": str(pin), "gis_id": str(gis_id),
                    "similarity": round(score, 3),
                })
        suggestions.sort(key=lambda item: (-item["similarity"], item["official_address"]))
        return {
            "status": "unresolved",
            "reason": "No exact normalized Allen County site-address match",
            "review_suggestions": suggestions[:3],
        }
    if len(matches) != 1:
        return {"status": "ambiguous", "reason": "Multiple Allen County parcel identities match this address"}

    (pin, gis_id), site = next(iter(matches.items()))
    parcel_data = _json_get(PARCEL_QUERY, {
        "f": "json",
        "where": "GIS_ID='" + gis_id.replace("'", "''") + "'",
        "outFields": (
            "PIN,GIS_ID,PropertyAddress1,PropertyCity,PropertyState,Zip_Code,"
            "OwnerofRecord,Property_Class_Description,Total_Value,Sales_Price,Sale_Date,YearBuilt,Legal_Acreage"
        ),
        "returnGeometry": "false", "resultRecordCount": "5",
    }, request=request)
    parcel_rows = [
        row for row in _rows(parcel_data)
        if str(row.get("GIS_ID", "")) == gis_id and str(row.get("PIN", "")) == pin
    ]
    if len(parcel_rows) != 1:
        return {"status": "unresolved", "reason": "Official parcel layer did not confirm one matching parcel"}

    parcel = parcel_rows[0]
    return {
        "status": "resolved",
        "pin": pin,
        "gis_id": gis_id,
        "official_address": site.get("fulladdr"),
        "zip": str(site.get("ZIP") or ""),
        "municipality": site.get("municipality") or "",
        "owner_of_record": parcel.get("OwnerofRecord") or "",
        "property_class": parcel.get("Property_Class_Description") or "",
        "assessed_total": parcel.get("Total_Value"),
        "prior_sale_price": parcel.get("Sales_Price"),
        "prior_sale_date": parcel.get("Sale_Date"),
        "year_built": parcel.get("YearBuilt"),
        "legal_acreage": parcel.get("Legal_Acreage"),
        "site_address_service": SITE_ADDRESS_QUERY.rsplit("/query", 1)[0],
        "parcel_service": PARCEL_QUERY.rsplit("/query", 1)[0],
    }
