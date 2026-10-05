from concurrent.futures import ThreadPoolExecutor
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from app import providers
from app.providers import AllenCountyAdapter, parcel_key
from app.server import create_server
from app.service import Application
from tests.research_fixture import SyntheticParcelAdapter


KEY = "029999999999999999"


def setup_research(tmp_path, address="123 Browser Example Street"):
    app = Application(tmp_path / "research.db")
    app.research_adapter = SyntheticParcelAdapter()
    prop = app.create_property({"address": address, "city": "Fort Wayne", "state": "IN"})
    return app, prop["id"]


def test_snapshot_requires_identity_review_and_acceptance_is_idempotent(tmp_path):
    app, pid = setup_research(tmp_path)
    snapshot = app.lookup_parcel(pid, {"parcel_key": KEY})
    assert snapshot["status"] == "pending"
    assert all(v["matches"] for v in snapshot["identity"].values())
    assert not app.state()["facts"]
    assert app.lookup_parcel(pid, {"parcel_key": KEY})["id"] == snapshot["id"]
    assert app.research_adapter.calls == 1
    with pytest.raises(ValueError, match="confirm the parcel identity"):
        app.review_research(snapshot["id"], {"decision": "accept", "note": "Unconfirmed"})
    request = {"decision": "accept", "note": "Synthetic owner identity reviewed", "owner_confirmed_identity": True}
    accepted = app.review_research(snapshot["id"], request)
    assert accepted["status"] == "accepted"
    assert len(app.state()["facts"]) == 7
    assert app.review_research(snapshot["id"], request)["fact_ids"] == accepted["fact_ids"]
    assert len(app.state()["sources"]) == 1
    source = app.state()["sources"][0]
    assert source["external_id"] == KEY
    assert source["content_hash"] and source["raw_reference"] == "research_snapshot:" + snapshot["id"]
    assert all(f["source_id"] == source["id"] for f in app.state()["facts"])


def test_mismatched_address_requires_explanation_and_rejection_imports_nothing(tmp_path):
    app, pid = setup_research(tmp_path, "456 Different St")
    snapshot = app.lookup_parcel(pid, {"parcel_key": KEY})
    assert snapshot["identity"]["address"]["matches"] is False
    with pytest.raises(ValueError, match="mismatch_explanation"):
        app.review_research(snapshot["id"], {"decision": "accept", "note": "Not explained", "owner_confirmed_identity": True})
    assert not app.state()["sources"]
    app.review_research(snapshot["id"], {"decision": "reject", "note": "Different parcel"})
    assert not app.state()["facts"]
    assert app.state()["research"][0]["status"] == "rejected"


def test_network_failures_are_saved_without_evidence_and_request_budget_is_bounded(tmp_path):
    app, pid = setup_research(tmp_path)
    class FailedAdapter:
        def fetch(self, key):
            raise TimeoutError("Provider not reachable")
    app.research_adapter = FailedAdapter()
    for _ in range(20):
        with pytest.raises(ValueError, match="lookup failed"):
            app.lookup_parcel(pid, {"parcel_key": KEY})
    with pytest.raises(ValueError, match="Daily parcel lookup budget"):
        app.lookup_parcel(pid, {"parcel_key": KEY})
    state = app.state()
    assert len(state["research"]) == 20
    assert all(s["status"] == "failed" for s in state["research"])
    assert not state["sources"] and not state["facts"]


def test_concurrent_lookup_does_not_duplicate_external_requests(tmp_path):
    app, pid = setup_research(tmp_path)
    started, release = threading.Event(), threading.Event()
    adapter = app.research_adapter
    class BlockingAdapter:
        def fetch(self, key):
            started.set()
            assert release.wait(timeout=5)
            return adapter.fetch(key)
    app.research_adapter = BlockingAdapter()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(app.lookup_parcel, pid, {"parcel_key": KEY})
        assert started.wait(timeout=5)
        try:
            with pytest.raises(ValueError, match="already in progress"):
                app.lookup_parcel(pid, {"parcel_key": KEY})
        finally:
            release.set()
        assert first.result(timeout=5)["status"] == "pending"
    assert adapter.calls == 1


@pytest.mark.parametrize("value", ["' OR 1=1", "https://example.com", "039999999999999999", "0299", 123, "02-9999999999999999a"])
def test_parcel_key_cannot_be_arbitrary_sql_or_url(value):
    with pytest.raises(ValueError):
        parcel_key(value)


def test_provider_maps_only_whitelisted_fields_and_keeps_unknowns_unknown(monkeypatch):
    payload = {"features": [{"attributes": {
        "GISPublished.SDE.Parcel_Poly.PIN": KEY,
        "sde.CurrentOwner.OwnerofRecord": "SYNTHETIC OWNER",
        "sde.CurrentOwner.PropertyAddress1": None,
        "sde.CurrentOwner.MailingAddress1": "Must not be imported",
        "sde.CurrentOwner.TransferDate": 0,
    }}]}
    import httpx
    def respond(request):
        assert "MailingAddress" not in str(request.url)
        assert str(request.url).startswith(providers.COUNTY_LAYER + "/query?")
        return httpx.Response(200, json=payload)
    adapter = AllenCountyAdapter(transport=httpx.MockTransport(respond))
    result = adapter.fetch(KEY)
    assert result["record"] == {"parcel_id": KEY, "recorded_owner_name": "SYNTHETIC OWNER", "reported_transfer_date": "1970-01-01"}
    payload["features"][0]["attributes"]["GISPublished.SDE.Parcel_Poly.PIN"] = "028888888888888888"
    with pytest.raises(ValueError, match="different parcel"):
        adapter.fetch(KEY)


def test_http_research_and_review_roundtrip(tmp_path):
    app, pid = setup_research(tmp_path)
    server = create_server(tmp_path / "research.db", port=0, application=app)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = "http://127.0.0.1:" + str(server.server_address[1])
    def post(path, data):
        request = Request(base + path, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
        try: response = urlopen(request, timeout=5)
        except HTTPError as error: response = error
        with response: return response.status, json.load(response)
    try:
        status, snapshot = post("/api/properties/"+pid+"/research", {"parcel_key": KEY})
        assert status == 201
        status, accepted = post("/api/research/"+snapshot["id"]+"/review", {"decision":"accept", "owner_confirmed_identity":True, "note":"Synthetic HTTP identity review"})
        assert status == 201 and accepted["status"] == "accepted"
        assert post("/api/properties/"+pid+"/research/extra", {"parcel_key":KEY})[0] == 404
    finally:
        server.shutdown();server.server_close();worker.join(timeout=5)
