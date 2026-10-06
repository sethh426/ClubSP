import json
from uuid import uuid4

import pytest

from app.parcel_resolution import canonical_address, resolve_parcel_identity
from app.service import Application


def arcgis_stub(site_rows, parcel_rows):
    def request(url, params):
        if "SiteAddresses_TrimbleUnity" in url:
            return {"features": [{"attributes": row} for row in site_rows]}
        if "AC_Parcel_iMap_org" in url:
            return {"features": [{"attributes": row} for row in parcel_rows]}
        raise AssertionError(url)
    return request


def candidate(address="10324 GREEN OAK BLVD", zip_code="46814"):
    return {"address": address, "city": "Fort Wayne", "state": "IN", "zip": zip_code}


def test_address_normalization_handles_county_format_variants():
    assert canonical_address("10324 GREEN OAK BLVD") == canonical_address("10324 GREENOAK BOULEVARD")
    assert canonical_address("1619 ST MARY'S AVE") == canonical_address("1619 SAINT MARYS AVENUE")
    assert canonical_address("2927 WESTBROOK DR B-206") != canonical_address("2927 WESTBROOK DR")


def test_resolver_confirms_one_site_address_and_parcel():
    site = [{
        "fulladdr": "10324 GREENOAK BLVD", "addrnum": "10324",
        "unittype": None, "unitid": None, "PIN": "021110327015000075",
        "GIS_ID": "02-11-10-327-015.000-075", "ZIP": "46814", "municipality": "FW",
    }]
    parcel = [{
        "PIN": "021110327015000075", "GIS_ID": "02-11-10-327-015.000-075",
        "PropertyAddress1": "10324 Greenoak Blvd", "PropertyCity": "Fort Wayne",
        "PropertyState": "IN", "Zip_Code": "46814",
        "Property_Class_Description": "1 Family Dwell - Platted Lot",
        "Total_Value": 200000, "Sales_Price": 150000, "Sale_Date": 1700000000000,
        "YearBuilt": 1998, "Legal_Acreage": 0.25,
    }]
    result = resolve_parcel_identity(candidate(), request=arcgis_stub(site, parcel))
    assert result["status"] == "resolved"
    assert result["gis_id"] == "02-11-10-327-015.000-075"
    assert result["pin"] == "021110327015000075"
    assert result["official_address"] == "10324 GREENOAK BLVD"
    assert result["property_class"].startswith("1 Family")


def test_resolver_rejects_unit_drop_and_ambiguous_parcel():
    site = [{
        "fulladdr": "2927 WESTBROOK DR", "addrnum": "2927",
        "PIN": "020735202000000074", "GIS_ID": "02-07-35-202-000.000-074",
        "ZIP": "46805", "municipality": "FW",
    }]
    unresolved = resolve_parcel_identity(
        candidate("2927 WESTBROOK DR B-206", "46805"),
        request=arcgis_stub(site, []),
    )
    assert unresolved["status"] == "unresolved"

    dup = [
        {**site[0], "fulladdr": "2927 WESTBROOK DR B-206"},
        {**site[0], "fulladdr": "2927 WESTBROOK DR B-206",
         "PIN": "020735202000001074", "GIS_ID": "02-07-35-202-000.001-074"},
    ]
    ambiguous = resolve_parcel_identity(
        candidate("2927 WESTBROOK DR B-206", "46805"),
        request=arcgis_stub(dup, []),
    )
    assert ambiguous["status"] == "ambiguous"


def test_resolver_requires_parcel_layer_confirmation():
    site = [{
        "fulladdr": "1619 SAINT MARYS AVE", "addrnum": "1619",
        "PIN": "020734485021000074", "GIS_ID": "02-07-34-485-021.000-074",
        "ZIP": "46808", "municipality": "FW",
    }]
    result = resolve_parcel_identity(
        candidate("1619 ST MARY'S AVE", "46808"),
        request=arcgis_stub(site, []),
    )
    assert result["status"] == "unresolved"
    assert "parcel layer" in result["reason"].lower()


def test_sheriff_retry_enriches_only_resolved_candidates(tmp_path):
    app = Application(tmp_path / "app.db")
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    record = {
        "id": str(uuid4()), "source_id": "sheriff_sales",
        "name": "Allen County Sheriff mortgage foreclosure sales",
        "url": "https://www.allencountysheriff.org/2026-sheriff-sales/",
        "fetched_at": now.isoformat(), "status": "scheduled_sales",
        "candidates": [
            {
                "address": "10324 GREEN OAK BLVD", "city": "Fort Wayne", "state": "IN", "zip": "46814",
                "cause_number": "02D03-1-MF-1", "sale_date": (now.date()).isoformat(),
                "judgment_amount": 100000, "parcel_ids": [], "minimum_bid": None,
                "intake_supported": False, "identity_note": "unresolved",
                "availability": "scheduled", "source_document_url": "https://example.test/a.pdf",
            },
            {
                "address": "2927 WESTBROOK DR B-206", "city": "Fort Wayne", "state": "IN", "zip": "46805",
                "cause_number": "02D03-1-MF-2", "sale_date": (now.date()).isoformat(),
                "judgment_amount": 50000, "parcel_ids": [], "minimum_bid": None,
                "intake_supported": False, "identity_note": "unresolved",
                "availability": "scheduled", "source_document_url": "https://example.test/a.pdf",
            },
        ],
        "excerpt": "fixture", "content_hash": "fixture", "changed": False,
    }
    with app.database.session(write=True) as (connection, _):
        connection.execute(
            "INSERT INTO discovery_checks VALUES(?,?,?,?)",
            (record["id"], "sheriff_sales", record["fetched_at"], json.dumps(record)),
        )

    def resolver(item):
        if item["address"].startswith("10324"):
            return {
                "status": "resolved", "pin": "021110327015000075",
                "gis_id": "02-11-10-327-015.000-075",
                "official_address": "10324 GREENOAK BLVD",
            }
        return {"status": "unresolved", "reason": "No exact normalized Allen County site-address match"}

    app.parcel_resolver = resolver
    saved = app.resolve_discovery_parcels({"check_id": record["id"]})
    assert saved["parcel_resolution_count"] == 1
    first, second = saved["candidates"]
    assert first["parcel_ids"] == ["02-11-10-327-015.000-075"]
    assert first["intake_supported"] is True
    assert second["parcel_ids"] == []
    assert second["intake_supported"] is False


def test_scheduled_sheriff_candidate_can_stage_only_after_resolved_identity(tmp_path):
    app = Application(tmp_path / "app.db")
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    check_id = str(uuid4())
    record = {
        "id": check_id, "source_id": "sheriff_sales",
        "name": "Allen County Sheriff mortgage foreclosure sales",
        "url": "https://www.allencountysheriff.org/2026-sheriff-sales/",
        "fetched_at": now.isoformat(), "status": "scheduled_sales",
        "candidates": [{
            "address": "1201 HANCOCK AVE", "city": "Fort Wayne", "state": "IN", "zip": "46803",
            "cause_number": "02D03-1-MF-1", "sale_date": now.date().isoformat(),
            "judgment_amount": 115905.46, "parcel_ids": ["02-13-07-202-001.000-074"],
            "minimum_bid": None, "intake_supported": True,
            "identity_note": "Parcel resolved from official GIS",
            "availability": "scheduled", "source_document_url": "https://example.test/a.pdf",
        }],
        "excerpt": "fixture", "content_hash": "fixture", "changed": False,
    }
    with app.database.session(write=True) as (connection, _):
        connection.execute(
            "INSERT INTO discovery_checks VALUES(?,?,?,?)",
            (check_id, "sheriff_sales", record["fetched_at"], json.dumps(record)),
        )
    staged = app.stage_discovery_intake({
        "check_id": check_id, "candidate_index": 0,
        "parcel_id": "02-13-07-202-001.000-074", "zip": "46803",
        "property_type": "single_family", "reviewer": "Owner",
        "note": "Confirmed official GIS parcel and current sheriff sale.",
        "identity_confirmed": True,
    })
    assert staged["status"] == "pending"
    assert not app.state()["properties"]
    assert not app.state()["deals"]
