"""Functional collector tests use synthetic responses, never live property facts."""
import calendar
from datetime import datetime, timedelta, timezone
import io
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import httpx
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
import pytest

from app.providers import FIELDS
from app.service import Application
from app.server import create_server
from app.auth import OwnerAuth, SESSION_COOKIE
from app.sentras import FIRST_25_SENTRA_IDS, SENTRAS
from app.sentra_collectors import SOURCE_SPECS


ADDRESS = "123 Example Rd, Fort Wayne, IN, 46802"
PARCEL = "020102100001000001"
PROPERTY = {"id": "synthetic-property", "addressLine1": "123 Example Rd", "city": "Fort Wayne",
            "state": "IN", "zipCode": "46802", "status": "Active", "price": 100000,
            "bedrooms": 3, "bathrooms": 2, "squareFootage": 1200,
            "owner": {"phone": "must-not-be-persisted"}}
INTENT = {"market": "Fort Wayne, IN", "property_types": ["single_family"], "max_total_price": 150000}


def inputs_for(key):
    kind = SOURCE_SPECS[key].kind
    if kind == "parcel_record":
        return {"parcel_key": PARCEL}
    if kind in {"sale_listing", "rental_listing", "inventory_count"}:
        return {"search_intent": INTENT}
    if kind == "county_housing_context":
        return {"state_fips": "18", "county_fips": "003"}
    if kind == "zip_market_context":
        return {"zip_code": "46802"}
    if "address" in SOURCE_SPECS[key].required_inputs:
        return {"address": ADDRESS}
    return {}


def synthetic_pdf():
    """A real PDF exercises extraction and the sheriff table parser together."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=1000, height=800)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%m/%d/%Y")
    text = f"1 {date} 02D01-2601-MF-000001 123 EXAMPLE RD FORT WAYNE, IN 46802 $ 25,000.00"
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 10 Tf 10 700 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def mock_source(request):
    path = request.url.path
    if path.endswith(".pdf"):
        return httpx.Response(200, content=synthetic_pdf(), headers={"content-type": "application/pdf"})
    if "sheriff-sales" in path:
        label = f"{calendar.month_name[datetime.now(timezone.utc).month].upper()} {datetime.now(timezone.utc).year}"
        return httpx.Response(200, text=f'<a href="/notices/current.pdf">{label}</a>', headers={"content-type": "text/html"})
    if path.endswith("/query"):
        assert request.url.params["returnGeometry"] == "false"
        assert request.url.params["where"] == "GISPublished.SDE.Parcel_Poly.PIN='" + PARCEL + "'"
        return httpx.Response(200, json={"features": [{"attributes": {next(iter(FIELDS)): PARCEL, "sde.CurrentOwner.OwnerofRecord": "Synthetic Owner"}}]})
    if request.url.host == "api.rentcast.io":
        assert request.headers["X-Api-Key"] == "synthetic-test-key"
        if path in {"/v1/listings/sale", "/v1/listings/rental/long-term"}:
            assert request.url.params["city"] == "Fort Wayne"
            assert request.url.params["status"] == "Active"
            assert int(request.url.params["limit"]) <= 50
            return httpx.Response(200, json=[PROPERTY])
        if path == "/v1/properties":
            assert request.url.params["address"] == ADDRESS
            return httpx.Response(200, json=[PROPERTY])
        if path == "/v1/markets":
            return httpx.Response(200, json={"zipCode": "46802", "saleData": {"averagePrice": 180000, "totalListings": 20}})
        prefix = "price" if path.endswith("/value") else "rent"
        return httpx.Response(200, json={prefix: 1200, prefix + "RangeLow": 1000, prefix + "RangeHigh": 1400, "subjectProperty": PROPERTY})
    if request.url.host == "api.realestateapi.com":
        assert request.method == "POST"
        assert json.loads(request.content)["count"] is True
        assert request.headers["X-Api-Key"] == "synthetic-test-key"
        return httpx.Response(200, json={"resultCount": 7})
    if request.url.host == "geocoding.geo.census.gov":
        return httpx.Response(200, json={"result": {"addressMatches": [{"matchedAddress": ADDRESS, "coordinates": {"x": -85.1, "y": 41.1}}]}})
    if request.url.host == "api.census.gov":
        fields = request.url.params["get"].split(",")
        values = ["Synthetic County" if field == "NAME" else "100" for field in fields]
        return httpx.Response(200, json=[fields + ["state", "county"], values + ["18", "003"]])
    html = '<title>Synthetic official source</title><h1>Synthetic source</h1><p>Property and tax source resources, for testing only.</p><a href="/notice.pdf">Property document</a>'
    if "ACCDC-Properties" in path:
        html += "<p>ACCDC Properties. Currently there are no properties available.</p>"
    if "North-Campus" in path:
        html += '''<h1>Sale of North Campus Property</h1><p>Synthetic property located at 123 Example Road, Fort Wayne, Indiana.
            02-01-02-100-001.000-001
            No bid for less than Example Dollars ($100,000.00) shall be considered.
            October 1, 2026, at 9 AM eastern time
            October 15, 2026, at 5 PM eastern time</p>'''
    return httpx.Response(200, text=html, headers={"content-type": "text/html"})


@pytest.fixture
def application(tmp_path, monkeypatch):
    monkeypatch.setenv("RENTCAST_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("REALESTATEAPI_API_KEY", "synthetic-test-key")
    app = Application(tmp_path / "sentra.sqlite3")
    app._sentra_transport = httpx.MockTransport(mock_source)
    return app


def run_data(key, **extra):
    return {"sentra_id": key, "input_data": inputs_for(key), "confirm_external_request": True, **extra}


def test_registry_has_exactly_25_real_collectors_and_no_meta_padding(application):
    assert len(FIRST_25_SENTRA_IDS) == len(set(FIRST_25_SENTRA_IDS)) == 25
    state = application.sentra_state()
    assert state["summary"]["source_collectors"] == 25
    assert state["summary"]["live_verified"] == 0
    assert all(row["health"] == "unverified" for row in state["sentras"])
    assert all(not SENTRAS[key].family.startswith("meta_") for key in FIRST_25_SENTRA_IDS)


@pytest.mark.parametrize("key", FIRST_25_SENTRA_IDS)
def test_every_collector_runs_through_transport_normalization_and_durable_evidence(application, key):
    run = application.run_sentra(run_data(key))
    assert run["status"] == "success", run
    result = run["result"]
    assert result["payload"]["schema_version"] == 1
    assert result["payload"]["kind"] == SOURCE_SPECS[key].kind
    assert result["payload"]["records"]
    assert all(row["review_state"] == "unreviewed" for row in result["payload"]["records"])
    assert result["metadata"]["requires_evidence_review"] is True
    assert result["metadata"]["http_requests"] <= SOURCE_SPECS[key].max_requests
    assert len(result["payload_sha256"]) == 64
    assert all(len(item["sha256"]) == 64 for item in result["metadata"]["evidence"])
    assert application.sentra_evidence(run["id"])["result"] == result
    assert "must-not-be-persisted" not in json.dumps(result)
    assert "synthetic-test-key" not in json.dumps(result)
    assert run["change"]["baseline"] is True
    assert application.state()["deals"] == []


def test_cache_and_idempotency_do_not_repeat_requests(application):
    key = "rentcast_sale_listings"
    first = application.run_sentra(run_data(key, idempotency_key="one"))
    repeated = application.run_sentra(run_data(key, idempotency_key="one"))
    cached = application.run_sentra(run_data(key))
    assert first["id"] == repeated["id"] == cached["id"]
    assert repeated["cached"] is cached["cached"] is True
    with application.database.session() as (connection, _):
        assert connection.execute("SELECT COUNT(*) FROM sentra_runs").fetchone()[0] == 1
    with pytest.raises(ValueError, match="different inputs"):
        application.run_sentra(run_data(key, idempotency_key="one", input_data={"search_intent": {"market": "Indianapolis, IN"}}))


def test_change_detection_compares_same_query_and_retains_history(application):
    key = "allen_county_tax_sale"
    first = application.run_sentra(run_data(key))
    application._sentra_transport = httpx.MockTransport(lambda _: httpx.Response(200, text="<title>Updated source</title><p>New property sale notice added to this synthetic source.</p>", headers={"content-type": "text/html"}))
    second = application.run_sentra(run_data(key, force_refresh=True))
    assert second["change"]["changed"] is True
    assert second["change"]["previous_run_id"] == first["id"]
    assert second["change"]["added_record_keys"] and second["change"]["removed_record_keys"]
    assert application.sentra_evidence(first["id"])["result"] == first["result"]


def test_failed_source_keeps_good_evidence_and_cools_down(application):
    key = "allen_county_tax_sale"
    first = application.run_sentra(run_data(key))
    application._sentra_transport = httpx.MockTransport(lambda _: httpx.Response(503, text="private error detail"))
    for _ in range(3):
        failed = application.run_sentra(run_data(key, force_refresh=True))
        assert failed["status"] == "failed"
        assert "private error detail" not in json.dumps(failed)
    with pytest.raises(ValueError, match="cooling down"):
        application.run_sentra(run_data(key, force_refresh=True))
    row = next(row for row in application.sentra_state()["sentras"] if row["id"] == key)
    assert row["health"] == "degraded" and row["last_success_at"]
    assert application.sentra_evidence(first["id"])["status"] == "success"


def test_shared_monthly_budget_covers_all_rentcast_collectors_and_legacy_calls(application, monkeypatch):
    monkeypatch.setenv("CLUBSP_RENTCAST_MONTHLY_REQUEST_CAP", "2")
    assert application.run_sentra(run_data("rentcast_sale_listings"))["status"] == "success"
    assert application.run_sentra(run_data("rentcast_market_statistics"))["status"] == "success"
    with pytest.raises(ValueError, match="Shared monthly"):
        application.run_sentra(run_data("rentcast_rent_estimate"))
    with application.database.session() as (connection, _):
        assert application._provider_usage(connection, "rentcast")["attempted_requests"] == 2


def test_missing_credentials_invalid_inputs_confirmation_and_kill_switch_use_no_budget(application, monkeypatch):
    monkeypatch.delenv("RENTCAST_API_KEY")
    with pytest.raises(ValueError, match="not configured"):
        application.run_sentra(run_data("rentcast_sale_listings"))
    with pytest.raises(ValueError, match="Unsupported collector input"):
        application.run_sentra(run_data("allen_county_tax_sale", input_data={"url": "https://unexpected.test"}))
    with pytest.raises(ValueError, match="confirm_external_request"):
        application.run_sentra({"sentra_id": "allen_county_tax_sale"})
    monkeypatch.setenv("CLUBSP_DISABLED_SENTRAS", "allen_county_tax_sale")
    with pytest.raises(ValueError, match="disabled"):
        application.run_sentra(run_data("allen_county_tax_sale"))
    with application.database.session() as (connection, _):
        assert connection.execute("SELECT COUNT(*) FROM sentra_runs").fetchone()[0] == 0


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"location": "https://unexpected.test"}),
    httpx.Response(200, text="<p>Verify you are human before accessing these property records.</p>", headers={"content-type": "text/html"}),
    httpx.Response(200, text="{}", headers={"content-type": "application/json"}),
    httpx.Response(200, text="x" * 500, headers={"content-type": "text/html"}),
])
def test_redirect_challenge_media_change_and_byte_limit_are_failures(application, response):
    calls = []
    def serve(request):
        calls.append(request)
        return response
    application._sentra_transport = httpx.MockTransport(serve)
    run = application.run_sentra(run_data("allen_county_tax_sale", max_bytes=400))
    assert run["status"] == "failed"
    assert len(calls) == 1


def test_wrong_parcel_and_ambiguous_address_do_not_import_evidence(application):
    application._sentra_transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"features": [{"attributes": {next(iter(FIELDS)): "020102100001000002"}}]}))
    assert application.run_sentra(run_data("allen_county_imap_parcel"))["status"] == "failed"
    application._sentra_transport = httpx.MockTransport(lambda _: httpx.Response(200, json=[PROPERTY, PROPERTY]))
    assert application.run_sentra(run_data("rentcast_property_record"))["status"] == "failed"


def test_new_query_is_a_baseline_and_empty_results_are_not_schema_drift(application):
    first = application.run_sentra(run_data("census_address_geocoder"))
    second = application.run_sentra(run_data("census_address_geocoder", input_data={"address": "124 Example Rd, Fort Wayne, IN 46802"}))
    assert first["change"]["baseline"] is second["change"]["baseline"] is True
    application._sentra_transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"result": {"addressMatches": []}}))
    empty = application.run_sentra(run_data("census_address_geocoder", force_refresh=True))
    assert empty["status"] == "success" and empty["result"]["payload"]["coverage_status"] == "no_match"
    assert empty["change"]["schema_changed"] is False


def test_source_run_reservation_prevents_concurrent_duplicate_fetch(application):
    entered, release = threading.Event(), threading.Event()
    def serve(request):
        entered.set()
        assert release.wait(5)
        return mock_source(request)
    application._sentra_transport = httpx.MockTransport(serve)
    key = "allen_county_tax_sale"
    output = []
    worker = threading.Thread(target=lambda: output.append(application.run_sentra(run_data(key))))
    worker.start()
    assert entered.wait(5)
    try:
        with pytest.raises(ValueError, match="in progress"):
            application.run_sentra(run_data(key))
    finally:
        release.set()
        worker.join(5)
    assert output[0]["status"] == "success"


def test_sentra_api_requires_owner_and_origin_and_serves_saved_evidence(application):
    auth = OwnerAuth(secret="synthetic-owner-secret-123456")
    server = create_server(application.database.path, port=0, application=application, auth=auth)
    origin = "http://127.0.0.1:" + str(server.server_address[1])
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    cookie = {"Cookie": SESSION_COOKIE + "=" + auth.issue()}
    try:
        with pytest.raises(HTTPError) as unauthorized:
            urlopen(origin + "/api/sentras")
        assert unauthorized.value.code == 401
        with urlopen(Request(origin + "/api/sentras", headers=cookie)) as response:
            assert json.load(response)["summary"]["source_collectors"] == 25
        body = json.dumps(run_data("allen_county_tax_sale")).encode()
        with pytest.raises(HTTPError) as no_origin:
            urlopen(Request(origin + "/api/sentras/run", data=body, headers={**cookie, "Content-Type": "application/json"}))
        assert no_origin.value.code == 403
        with urlopen(Request(origin + "/api/sentras/run", data=body, headers={**cookie, "Content-Type": "application/json", "Origin": origin})) as response:
            run = json.load(response)
        assert run["status"] == "success"
        with urlopen(Request(origin + "/api/sentras/runs/" + run["id"], headers=cookie)) as response:
            assert json.load(response)["result"] == run["result"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5)


def test_rental_budget_is_separate_from_property_acquisition_ceiling(application):
    from app.sentra_collectors import compile_source_request
    from app.sentra_execution import SentraExecutionRequest
    definition = SENTRAS["rentcast_rental_listings"]
    request = SentraExecutionRequest(definition.id, input_data={"search_intent": INTENT})
    assert "price" not in compile_source_request(definition, request)[1]
    request = SentraExecutionRequest(definition.id, input_data={"search_intent": INTENT, "max_monthly_rent": 1800})
    assert compile_source_request(definition, request)[1]["price"] == "*:1800"


def test_census_missing_value_sentinels_preserve_margins_of_error(application):
    def source(request):
        fields = request.url.params["get"].split(",")
        values = ["Synthetic County" if key == "NAME" else "-666666666" if key == "B25064_001E" else "100" for key in fields]
        return httpx.Response(200, json=[fields + ["state", "county"], values + ["18", "003"]])
    application._sentra_transport = httpx.MockTransport(source)
    run = application.run_sentra(run_data("census_county_housing"))
    values = run["result"]["payload"]["records"][0]["values"]
    assert values["B25064_001E"] is None
    assert values["B25064_001M"] == 100


def test_expired_run_lease_recovers_after_restart_without_resetting_budget(application):
    key = "allen_county_tax_sale"
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with application.database.session(write=True) as (connection, _):
        connection.execute("INSERT INTO sentra_runs(id,sentra_id,input_hash,budget_group,reserved_requests,business_day,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                           ("synthetic-crash", key, "a" * 64, key, 1, "2026-10-06", "running", old))
        connection.execute("UPDATE sentra_health SET current_run_id='synthetic-crash' WHERE sentra_id=?", (key,))
    restarted = Application(application.database.path)
    restarted._sentra_transport = httpx.MockTransport(mock_source)
    assert restarted.run_sentra(run_data(key))["status"] == "success"
    assert restarted.sentra_evidence("synthetic-crash")["status"] == "interrupted"
    with restarted.database.session() as (connection, _):
        assert connection.execute("SELECT SUM(reserved_requests) FROM sentra_runs").fetchone()[0] == 2


def test_sentra_configuration_file_is_data_only_and_preserves_environment(tmp_path, monkeypatch):
    from app.sentra_runtime import load_sentra_environment
    monkeypatch.setenv("RENTCAST_API_KEY", "existing-value")
    monkeypatch.delenv("REALESTATEAPI_API_KEY", raising=False)
    path = tmp_path / "config.env"
    path.write_text("RENTCAST_API_KEY=file-value\nREALESTATEAPI_API_KEY='synthetic-file-value'\nUNRECOGNIZED_SENTRA_KEY=ignored\n")
    load_sentra_environment(path)
    import os
    assert os.environ["RENTCAST_API_KEY"] == "existing-value"
    assert os.environ["REALESTATEAPI_API_KEY"] == "synthetic-file-value"
    assert "UNRECOGNIZED_SENTRA_KEY" not in os.environ
