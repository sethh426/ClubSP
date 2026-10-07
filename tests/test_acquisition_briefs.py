"""Exercise real governed collectors using synthetic provider responses."""
from datetime import datetime, timedelta, timezone
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import httpx
import pytest

from app.acquisition_briefs import DEFAULTS, criteria, economics
from app.service import Application
from app.server import create_server

PROPERTY = {"id": "test-listing", "addressLine1": "123 Example Rd", "city": "Fort Wayne",
            "state": "IN", "zipCode": "46802", "propertyType": "Single Family",
            "status": "Active", "price": 100000, "bedrooms": 3, "bathrooms": 2}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("RENTCAST_API_KEY", "synthetic-test-key")
    return Application(tmp_path / "briefs.sqlite3")


def transport(calls, *, listing=None, bad_identity=False, fail_rent=False):
    def respond(request):
        calls.append(request)
        if request.url.path.endswith("/sale"):
            assert request.url.params["limit"] == "3"
            return httpx.Response(200, json=[PROPERTY if listing is None else listing])
        assert request.url.params["address"] == "123 Example Rd, Fort Wayne, IN, 46802"
        if fail_rent:
            return httpx.Response(403, json={"secret": "do-not-persist-provider-body"})
        subject = {**PROPERTY, "addressLine1": "999 Wrong Rd"} if bad_identity else PROPERTY
        return httpx.Response(200, json={"subjectProperty": subject, "rent": 1200,
                                        "rentRangeLow": 1000, "rentRangeHigh": 1400})
    return httpx.MockTransport(respond)


def test_math_has_known_cash_basis_and_lower_range_yield():
    assert economics(100000, 1000, DEFAULTS) == {"cash_basis": 113000, "annual_operating_income": 7728,
                                                "yield_pct": 6.84}


@pytest.mark.parametrize("data", [{"max_price": True}, {"min_beds": 2.5}, {"expense_pct": float("nan")},
                                    {"repair_reserve": -1}, {"market": "Other"}, {"max_price": "150000"}])
def test_invalid_criteria_never_call_provider(app, data):
    with pytest.raises(ValueError):
        app.build_acquisition_brief(data)
    assert app.acquisition_brief_history()["briefs"] == []


def test_build_provenance_cash_math_and_restart_reuse(app):
    calls = []
    app._sentra_transport = transport(calls)
    brief = app.build_acquisition_brief({})
    assert brief["status"] == "ready"
    assert len(calls) == 2
    assert brief["result"]["cards"][0]["economics"]["yield_pct"] == 6.84
    assert brief["result"]["cards"][0]["screen"] == "meets_assumed_yield"
    for evidence in brief["result"]["evidence"]:
        assert app.sentra_evidence(evidence["run_id"])["status"] == "success"
    restarted = Application(app.database.path)
    restarted._sentra_transport = transport(calls)
    assert restarted.build_acquisition_brief({})["reused"] is True
    assert len(calls) == 2
    assert restarted.acquisition_brief_history()["briefs"][0]["id"] == brief["id"]


@pytest.mark.parametrize("changes", [{"propertyType": "Condo"}, {"bedrooms": None}, {"price": 200000}, {"price": 0}])
def test_missing_or_mismatched_criteria_excluded_without_rent_calls(app, changes):
    calls = []
    app._sentra_transport = transport(calls, listing={**PROPERTY, **changes})
    brief = app.build_acquisition_brief({})
    assert len(calls) == 1
    assert brief["status"] == "incomplete"
    assert brief["result"]["cards"] == []
    assert brief["result"]["excluded_count"] == 1


@pytest.mark.parametrize("failure", ["bad_identity", "fail_rent"])
def test_estimate_failure_cannot_become_zero_expense_or_profitable_card(app, failure):
    app._sentra_transport = transport([], **{failure: True})
    brief = app.build_acquisition_brief({})
    assert brief["status"] == "incomplete"
    card = brief["result"]["cards"][0]
    assert card["economics"] is None and card["rent_estimate"] is None
    assert "do-not-persist-provider-body" not in json.dumps(brief)


def test_shared_quota_stops_brief_without_network_or_fabricated_records(app, monkeypatch):
    monkeypatch.setenv("CLUBSP_RENTCAST_MONTHLY_REQUEST_CAP", "1")
    calls = []
    app._sentra_transport = transport(calls)
    brief = app.build_acquisition_brief({})
    assert len(calls) == 1
    assert brief["result"]["cards"][0]["economics"] is None
    assert brief["result"]["warnings"]


def test_duplicate_builds_are_serialized(app):
    started, release = threading.Event(), threading.Event()
    calls = []
    inner = transport(calls)
    def slow(request):
        started.set()
        assert release.wait(5)
        return inner.handle_request(request)
    app._sentra_transport = httpx.MockTransport(slow)
    output = []
    thread = threading.Thread(target=lambda: output.append(app.build_acquisition_brief({})))
    thread.start()
    assert started.wait(5)
    try:
        with pytest.raises(ValueError, match="being built"):
            app.build_acquisition_brief({})
    finally:
        release.set()
        thread.join(5)
    assert output[0]["status"] == "ready" and len(calls) == 2


def test_expired_worker_resumes_saved_child_runs_without_extra_requests(app):
    calls = []
    app._sentra_transport = transport(calls)
    first = app.build_acquisition_brief({})
    old = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    with app.database.session(write=True) as (connection, _):
        connection.execute("UPDATE acquisition_briefs SET status='running',started_at=?,result_json='{}',completed_at='' WHERE id=?", (old, first["id"]))
    resumed = app.build_acquisition_brief({})
    assert resumed["id"] == first["id"] and resumed["status"] == "ready"
    assert len(calls) == 2


def test_three_candidates_cost_at_most_four_requests_and_rank_by_lower_rent(app):
    calls = []
    def respond(request):
        calls.append(request)
        if request.url.path.endswith('/sale'):
            return httpx.Response(200, json=[{**PROPERTY, 'id': str(index),
                'addressLine1': f'{index} Example Rd'} for index in (1, 2, 3)])
        index = int(request.url.params['address'].split(' ')[0])
        rent = index * 500
        return httpx.Response(200, json={'subjectProperty': {**PROPERTY, 'addressLine1': f'{index} Example Rd'},
                                        'rent': rent, 'rentRangeLow': rent - 100, 'rentRangeHigh': rent + 100})
    app._sentra_transport = httpx.MockTransport(respond)
    brief = app.build_acquisition_brief({})
    assert len(calls) == 4
    assert [card['listing']['address'] for card in brief['result']['cards']] == ['3 Example Rd', '2 Example Rd', '1 Example Rd']


def test_http_build_requires_matching_origin_and_persists(app):
    app._sentra_transport = transport([])
    server = create_server(app.database.path, 0, application=app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        for origin in (None, 'https://other.example'):
            headers = {'Content-Type': 'application/json'}
            if origin:
                headers['Origin'] = origin
            with pytest.raises(HTTPError) as error:
                urlopen(Request(base + '/api/sentras/briefs/build', data=b'{}', headers=headers))
            assert error.value.code == 403
        with urlopen(Request(base + '/api/sentras/briefs/build', data=b'{}',
                     headers={'Content-Type': 'application/json', 'Origin': base})) as response:
            brief = json.load(response)
        with urlopen(base + '/api/sentras/briefs') as response:
            assert json.load(response)['briefs'][0]['id'] == brief['id']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
