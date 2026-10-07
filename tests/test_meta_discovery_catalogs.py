import httpx
import pytest

import app.meta_sentry_service as meta
from app.meta_source_transport import fetch_source
from app.service import Application


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    app = Application(tmp_path / "clubsp.db")
    fixture = {"payload": {}, "status": 200, "requests": []}
    def handle(request):
        fixture["requests"].append(request)
        return httpx.Response(fixture["status"], json=fixture["payload"])
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(meta, "fetch_source", lambda url, **kwargs: fetch_source(url, transport=transport, **kwargs))
    return app, fixture


def test_modern_datagov_catalog_uses_resource_and_required_key(catalog, monkeypatch):
    app, fixture = catalog
    monkeypatch.setenv("DATAGOV_API_KEY", "synthetic-test-key")
    fixture["payload"] = {"after": "next-page", "results": [{
        "identifier": "dataset-1", "title": "County parcel property sales",
        "organization": {"name": "Example County"},
        "dcat": {"accessLevel": "public", "landingPage": "https://example.gov/landing",
                 "distribution": [{"mediaType": "application/json", "downloadURL": "https://example.gov/data.json"}]},
    }]}
    result = app.meta_discover({"provider": "data_gov", "query": "parcel sales"})
    assert result["new_quarantined"] == 1 and result["next_cursor"] == "next-page"
    request = fixture["requests"][0]
    assert request.url.host == "api.gsa.gov" and request.headers["X-Api-Key"] == "synthetic-test-key"
    state = app.meta_sentra_state()
    assert state["candidates"][0]["source_url"] == "https://example.gov/data.json"
    assert state["candidates"][0]["state"] == "quarantined"
    assert state["summary"]["active"] == 0


@pytest.mark.parametrize("key", ["", "DEMO_KEY"])
def test_datagov_missing_production_key_is_recorded_without_request(catalog, monkeypatch, key):
    app, fixture = catalog
    monkeypatch.setenv("DATAGOV_API_KEY", key)
    with pytest.raises(ValueError, match="DATAGOV_API_KEY"):
        app.meta_discover({"provider": "data_gov", "query": "parcels"})
    assert fixture["requests"] == []
    runs = Application(app.database.path).meta_sentra_state()["runs"]
    assert len(runs) == 1 and runs[0]["status"] == "failed"


def test_apify_catalog_quarantines_actors_without_executing_them(catalog):
    app, fixture = catalog
    fixture["payload"] = {"data": {"total": 30, "items": [{
        "id": "actor-1", "username": "example", "name": "parcel-scraper",
        "title": "Parcel Assessor GIS Scraper", "description": "property sales", "pricingModel": "PAY_PER_USAGE",
    }]}}
    result = app.meta_discover({"provider": "apify_store", "query": "parcels"})
    assert result["new_quarantined"] == 1 and result["next_cursor"] == "1"
    assert fixture["requests"][0].url.path == "/v2/store"
    state = app.meta_sentra_state()
    assert state["summary"]["quarantined"] == 1 and state["summary"]["active"] == 0
    with pytest.raises(ValueError, match="discovery-only"):
        app.meta_probe({"fingerprint": state["candidates"][0]["fingerprint"]})


def test_arcgis_infers_capabilities_without_treating_owner_as_jurisdiction(catalog):
    app, fixture = catalog
    fixture["payload"] = {"nextStart": 26, "results": [{
        "id": "arcgis-item", "title": "County Parcel Assessor Sales", "snippet": "GIS property sale records",
        "url": "https://gis.example.gov/rest/services/Parcels/FeatureServer/0", "owner": "account-name", "access": "public",
    }]}
    result = app.meta_discover({"provider": "arcgis_online", "query": "parcels"})
    record = app.meta_sentra_state()["candidates"][0]
    assert "parcel_identity" in record["capabilities"] and record["jurisdiction_hint"] == ""
    assert result["next_cursor"] == "26"
    assert 'access:"public"' in fixture["requests"][0].url.params["q"]


def test_ckan_discovery_is_scoped_and_prefers_actual_resources(catalog):
    app, fixture = catalog
    fixture["payload"] = {"success": True, "result": {"count": 2, "results": [{
        "id": "dataset-1", "title": "Parcel sales", "url": "https://example.gov/landing",
        "resources": [{"format": "CSV", "url": "https://example.gov/sales.csv"}],
    }]}}
    result = app.meta_discover({"provider": "ckan", "query": "parcels", "catalog_url": "https://data.example.gov"})
    assert result["next_cursor"] == "1"
    assert fixture["requests"][0].url.path == "/api/3/action/package_search"
    record = app.meta_sentra_state()["candidates"][0]
    assert record["source_url"] == "https://example.gov/sales.csv"
    assert record["external_id"].startswith("https://data.example.gov#")


@pytest.mark.parametrize("payload", [{}, {"error": {"code": 500}}, {"success": False}, {"results": "broken"}])
def test_catalog_failure_is_durable_for_manual_calls(catalog, payload):
    app, fixture = catalog
    fixture["payload"] = payload
    with pytest.raises(ValueError):
        app.meta_discover({"provider": "arcgis_online", "query": "parcels"})
    state = Application(app.database.path).meta_sentra_state()
    assert state["summary"]["total"] == 0
    assert len(state["runs"]) == 1 and state["runs"][0]["status"] == "failed"


def test_cycle_continues_after_provider_failure_and_does_not_double_record(catalog):
    app, fixture = catalog
    fixture["status"] = 503
    result = app.meta_discovery_cycle({"providers": ["arcgis_online", "apify_store"], "max_queries": 1})
    assert result["failures"] == 2 and result["attempts"] == 2
    assert len(app.meta_sentra_state()["runs"]) == 2
    assert app.meta_sentra_state()["summary"]["active"] == 0


def test_cycle_rejects_duplicate_providers_and_boolean_budgets(catalog):
    app, fixture = catalog
    for data in ({"providers": ["arcgis_online", "arcgis_online"]}, {"max_queries": True}):
        with pytest.raises(ValueError):
            app.meta_discovery_cycle(data)
    assert fixture["requests"] == []
