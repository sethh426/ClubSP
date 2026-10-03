"""A fixed, bounded, read-only public record adapter. No arbitrary URLs."""
from datetime import datetime, timezone
import re
from urllib.request import HTTPRedirectHandler
from pydantic import BaseModel, ConfigDict, Field
from .provider_http import BoundedJSONTransport


COUNTY_LAYER = "https://gis.acimap.us/services/rest/services/CFW/Parcels_With_Ownership_Information/MapServer/0"
PROVIDER = {
    "id": "allen_county_imap", "name": "Allen County, Indiana iMap owner record",
    "home": "https://www.acimap.us/", "endpoint": COUNTY_LAYER,
    "scope": "One exact Allen County parcel key per lookup", "credentials": "none",
    "daily_request_limit": 20, "cache_hours": 24, "timeout_seconds": 10,
    "rights_note": "Public single-record lookup; no bulk-export license is assumed.",
    "limits": "Owner-of-record and site-address evidence only. No appraisal, sold comps, title clearance, contact consent or verified seller authority.",
    "metadata_checked_on": "2026-09-30",
}
FIELDS = {
    "GISPublished.SDE.Parcel_Poly.PIN": "parcel_id",
    "sde.CurrentOwner.OwnerofRecord": "recorded_owner_name",
    "sde.CurrentOwner.PropertyAddress1": "official_property_address",
    "sde.CurrentOwner.PropertyAddress2": "official_property_address_line2",
    "sde.CurrentOwner.PropertyCity": "official_property_city",
    "sde.CurrentOwner.PropertyState": "official_property_state",
    "sde.CurrentOwner.PropertyZip": "official_property_zip",
    "sde.CurrentOwner.TransferDate": "reported_transfer_date",
    "sde.CurrentOwner.PayYear": "record_pay_year",
}


def parcel_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9\- ]{18,30}", value.strip()):
        raise ValueError("Enter an 18-digit Allen County parcel key, with optional hyphens")
    key = re.sub(r"[- ]", "", value.strip())
    if len(key) != 18 or not key.startswith("02"):
        raise ValueError("Allen County parcel keys must contain 18 digits and start with 02")
    return key


def normalized_address(value):
    aliases = {"STREET": "ST", "ROAD": "RD", "AVENUE": "AVE", "DRIVE": "DR", "LANE": "LN", "BOULEVARD": "BLVD", "COURT": "CT", "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "INDIANA": "IN"}
    return " ".join(aliases.get(token, token) for token in re.findall(r"[A-Z0-9]+", str(value).upper()))


class NoRedirect(HTTPRedirectHandler):
    """Used by the existing bounded HTML source adapters."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Provider redirected; review the configured endpoint")


class ParcelFeature(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", allow_inf_nan=False)
    attributes: dict[str, str | int | float | None]


class ParcelResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    features: list[ParcelFeature] = Field(max_length=2)
    exceededTransferLimit: bool = False


class AllenCountyAdapter:
    def __init__(self, *, transport=None):
        self.transport = BoundedJSONTransport(COUNTY_LAYER + "/query", transport=transport)

    def fetch(self, key):
        key = parcel_key(key)
        url, payload = self.transport.fetch({
            "where": "GISPublished.SDE.Parcel_Poly.PIN='" + key + "'",
            "outFields": ",".join(FIELDS), "returnGeometry": "false", "f": "json"})
        if payload.get("error"):
            raise ValueError("Provider returned an error; no evidence was imported")
        parsed = ParcelResponse.model_validate(payload)
        if parsed.exceededTransferLimit:
            raise ValueError("Provider truncated the response; identity is ambiguous")
        features = parsed.features
        if not features:
            return {"url": url, "record": None, "status": "no_match"}
        if len(features) != 1:
            return {"url": url, "record": None, "status": "ambiguous"}
        attrs = features[0].attributes
        if not isinstance(attrs, dict) or "GISPublished.SDE.Parcel_Poly.PIN" not in attrs:
            raise ValueError("Provider schema changed; parcel identity field is missing")
        if parcel_key(str(attrs["GISPublished.SDE.Parcel_Poly.PIN"])) != key:
            raise ValueError("Provider returned a different parcel; evidence was not imported")
        record = {}
        for field, name in FIELDS.items():
            value = attrs.get(field)
            if value is None or value == "":
                continue
            if name == "reported_transfer_date":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("Provider transfer date is malformed")
                value = datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
                if value > datetime.now(timezone.utc).date().isoformat():
                    raise ValueError("Provider transfer date is in the future")
            if isinstance(value, str):
                value = value.strip()
                if len(value) > 1000:
                    raise ValueError("Provider field exceeds the supported record size")
            elif not isinstance(value, int) or isinstance(value, bool):
                raise ValueError("Provider field has an unsupported format")
            if value != "":
                record[name] = value
        return {"url": url, "record": record, "status": "pending"}
