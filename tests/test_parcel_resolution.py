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
    assert canonical_address("2927 WESTBROOK DR B-206") == canonical_address("2927 WESTBROOK DR UNIT B206")
    assert canonical_address("4117 E SADDLE DR") == canonical_address("4117 SADDLE DR E")


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


def test_resolver_accepts_exact_unit_and_directional_format_variants():
    westbrook_site = [{
        "fulladdr": "2927 WESTBROOK DR UNIT B206", "addrnum": "2927",
        "unittype": "UNIT", "unitid": "B206", "PIN": "020735202000000074",
        "GIS_ID": "02-07-35-202-000.000-074", "ZIP": "46805", "municipality": "FW",
    }]
    westbrook_parcel = [{
        "PIN": "020735202000000074", "GIS_ID": "02-07-35-202-000.000-074",
        "PropertyAddress1": "2927 Westbrook Dr", "PropertyCity": "Fort Wayne",
        "PropertyState": "IN", "Zip_Code": "46805", "Property_Class_Description": "Condominium",
        "Total_Value": 90000, "Sales_Price": 80000, "Sale_Date": 1700000000000,
        "YearBuilt": 1985, "Legal_Acreage": 0,
    }]
    unit = resolve_parcel_identity(
        candidate("2927 WESTBROOK DR B-206", "46805"),
        request=arcgis_stub(westbrook_site, westbrook_parcel),
    )
    assert unit["status"] == "resolved"
    assert unit["official_address"] == "2927 WESTBROOK DR UNIT B206"

    saddle_site = [{
        "fulladdr": "4117 SADDLE DR E", "addrnum": "4117",
        "unittype": None, "unitid": None, "PIN": "021115479013000075",
        "GIS_ID": "02-11-15-479-013.000-075", "ZIP": "46804", "municipality": "FW",
    }]
    saddle_parcel = [{
        "PIN": "021115479013000075", "GIS_ID": "02-11-15-479-013.000-075",
        "PropertyAddress1": "4117 Saddle Dr E", "PropertyCity": "Fort Wayne",
        "PropertyState": "IN", "Zip_Code": "46804", "Property_Class_Description": "1 Family Dwell",
        "Total_Value": 200000, "Sales_Price": 0, "Sale_Date": None,
        "YearBuilt": 2000, "Legal_Acreage": 0.2,
    }]
    directional = resolve_parcel_identity(
        candidate("4117 E SADDLE DR", "46804"),
        request=arcgis_stub(saddle_site, saddle_parcel),
    )
    assert directional["status"] == "resolved"


def test_resolver_surfaces_near_match_but_does_not_auto_resolve_spelling_or_suffix_conflict():
    site = [{
        "fulladdr": "3102 CRESTMONT DR", "addrnum": "3102",
        "PIN": "021331278003000077", "GIS_ID": "02-13-31-278-003.000-077",
        "ZIP": "46816", "municipality": "FW",
    }]
    result = resolve_parcel_identity(
        candidate("3102 CRESMONT DR", "46816"),
        request=arcgis_stub(site, []),
    )
    assert result["status"] == "unresolved"
    assert result["review_suggestions"][0]["official_address"] == "3102 CRESTMONT DR"

    suffix = [{
        "fulladdr": "318 MCKINNIE AVE", "addrnum": "318",
        "PIN": "021223227023000074", "GIS_ID": "02-12-23-227-023.000-074",
        "ZIP": "46806", "municipality": "FW",
    }]
    result2 = resolve_parcel_identity(
        candidate("318 MCKINNIE DR", "46806"),
        request=arcgis_stub(suffix, []),
    )
    assert result2["status"] == "unresolved"
    assert result2["review_suggestions"][0]["official_address"] == "318 MCKINNIE AVE"
