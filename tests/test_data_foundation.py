"""Synthetic responses and evidence only; no live requests or paid services."""
from copy import deepcopy
from datetime import timedelta
import json

import httpx
import pytest

from app.provider_http import BoundedJSONTransport
from app.providers import AllenCountyAdapter, COUNTY_LAYER
from app.service import Application
from app.comparables import comparable_screen
from core.memory.models import utc_now
from tests.test_sourcing import accept_sale, add_fact

KEY = '029999999999999999'


def adapter(payload, status=200, headers=None):
    calls = []
    def respond(request):
        calls.append(request)
        assert str(request.url).startswith(COUNTY_LAYER + '/query?')
        return httpx.Response(status, json=payload, headers=headers)
    return AllenCountyAdapter(transport=httpx.MockTransport(respond)), calls


@pytest.mark.parametrize('payload', [
    {'features': [{}]}, {'features': 'bad'}, {'features': [], 'exceededTransferLimit': True},
    {'features': [], 'error': {'message': 'private details'}},
    {'features': [{'attributes': {'GISPublished.SDE.Parcel_Poly.PIN': KEY, 'sde.CurrentOwner.OwnerofRecord': True}}]},
    {'features': [{'attributes': {'GISPublished.SDE.Parcel_Poly.PIN': KEY, 'sde.CurrentOwner.TransferDate': 9999999999999}}]},
    {'features': [{'attributes': {'GISPublished.SDE.Parcel_Poly.PIN': KEY}}] * 3},
])
def test_provider_rejects_schema_drift_and_incomplete_results(payload):
    provider, calls = adapter(payload)
    with pytest.raises(ValueError):
        provider.fetch(KEY)
    assert len(calls) == 1


@pytest.mark.parametrize('status', [302, 429, 500])
def test_http_failure_never_redirects_retries_or_exposes_body(status):
    provider, calls = adapter({'secret': 'do not surface'}, status, {'Location': 'https://example.com'})
    with pytest.raises(ValueError) as error:
        provider.fetch(KEY)
    assert 'secret' not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize('body,ctype', [(b'not json', 'application/json'), (b'{"value":NaN}', 'application/json'),
                                        (b'[]', 'application/json'), (b'{"x":1,"x":2}', 'application/json'), (b'{}', 'text/html'),
                                        (b' ' * 65, 'application/json')])
def test_bounded_json_validation(body, ctype):
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=body, headers={'Content-Type': ctype}))
    with pytest.raises(ValueError):
        BoundedJSONTransport('https://example.com/fixed', transport=transport, max_bytes=64).fetch({})


def test_timeout_is_a_saved_failure_without_retry(tmp_path):
    app = Application(tmp_path / 'timeouts.db')
    prop = app.create_property({'address': 'Synthetic', 'city': 'Fort Wayne', 'state': 'IN'})
    calls = []
    def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout('sensitive upstream response')
    app.research_adapter = AllenCountyAdapter(transport=httpx.MockTransport(fail))
    with pytest.raises(ValueError, match='Official record lookup failed'):
        app.lookup_parcel(prop['id'], {'parcel_key': KEY})
    saved = app.state()['research'][0]
    assert saved['status'] == 'failed'
    assert 'sensitive' not in saved['error']
    assert len(calls) == 1
    assert not app.state()['facts']


def screen_fixture():
    today = utc_now().date()
    values = {'parcel_id': '02-9999999999999999', 'property_class': 'Residential',
              'neighborhood_code': '001', 'living_area': 1800.0, 'year_built': 1980.0,
              'bath': 2.0, 'acreage': 0.2}
    evidence = {'values': values, 'facts': [], 'conflicts': [], 'digest': 'subject'}
    prop = {'id': 'subject', 'address': 'Synthetic subject', 'city': 'Fort Wayne', 'state': 'IN'}
    sale = {'parcel_id': '028888888888888888', 'address': 'Synthetic comp', 'city': 'Fort Wayne', 'state': 'IN',
            'property_class': 'Residential', 'neighborhood_code': '001', 'living_area': 1900.0,
            'year_built': 1985.0, 'bath': 2.0, 'acreage': 0.21, 'sale_price': 240000.0,
            'sale_date': today.isoformat()}
    snapshot = {'items': [{'id': 'sale', 'sale': sale, 'source': {'source_url': 'https://example.com', 'source_date': today.isoformat()}}],
                'conflicts': False, 'digest': 'sales'}
    return prop, evidence, snapshot


def test_comparable_fit_is_explained_without_estimated_value():
    args = screen_fixture()
    before = deepcopy(args)
    result = comparable_screen(*args)
    item = result['items'][0]
    assert item['decision'] == 'screening_fit'
    assert item['historical_price_per_sqft'] == 126.32
    assert item['comparisons']['living_area']['difference'] == pytest.approx(100 / 1800)
    assert result['valuation_available'] is False and result['execution_authorized'] is False
    assert args == before


@pytest.mark.parametrize('field,value', [('parcel_id', KEY), ('city', 'Indianapolis'), ('property_class', 'Commercial'),
    ('neighborhood_code', '999'), ('living_area', 3000.0), ('year_built', 2001.0), ('bath', 4.0), ('acreage', 0.5),
    ('sale_date', (utc_now().date() - timedelta(days=366)).isoformat()),
    ('sale_date', (utc_now().date() + timedelta(days=1)).isoformat())])
def test_comparable_exclusions(field, value):
    prop, evidence, snapshot = screen_fixture()
    snapshot['items'][0]['sale'][field] = value
    item = comparable_screen(prop, evidence, snapshot)['items'][0]
    assert item['decision'] == 'excluded_by_screen'
    assert item['reasons']


@pytest.mark.parametrize('value', [None, True, '1800', float('nan'), float('inf'), -1])
def test_bad_or_missing_subject_measurement_requires_review(value):
    prop, evidence, snapshot = screen_fixture()
    evidence['values']['living_area'] = value
    assert comparable_screen(prop, evidence, snapshot)['items'][0]['decision'] == 'needs_review'


def test_conflicts_and_staleness_require_review():
    prop, evidence, snapshot = screen_fixture()
    evidence['conflicts'] = ['property_class']
    evidence['facts'] = [{'attribute': 'living_area', 'observed_at': (utc_now() - timedelta(days=31)).isoformat()}]
    item = comparable_screen(prop, evidence, snapshot)['items'][0]
    assert item['decision'] == 'needs_review'
    assert any('conflicting subject' in r for r in item['reasons'])
    assert any('Refresh subject living_area' in r for r in item['reasons'])


def test_live_screen_changes_on_fact_correction_and_withdrawal_after_restart(tmp_path):
    app = Application(tmp_path / 'comps.db')
    prop = app.create_property({'address': 'Synthetic subject', 'city': 'Fort Wayne', 'state': 'IN'})
    pid = prop['id']
    _, evidence, _ = screen_fixture()
    facts = {field: add_fact(app, pid, field, value) for field, value in evidence['values'].items()}
    sale = accept_sale(app, pid, csv='Parcel Number,Address,Sale Date,Sale Price,Living Area,Property Class,Neighborhood Code,Year Built,Bath,Acreage\n028888888888888888,Synthetic comp,' + utc_now().date().isoformat() + ',240000,1900,Residential,001,1985,2,0.21')
    original = app.state()['sourcing']['comparable_screens'][0]
    assert original['items'][0]['decision'] == 'screening_fit'
    app.record_fact({'property_id': pid, 'attribute': 'living_area', 'value': 5000.0,
                     'provider': 'Synthetic correction', 'confidence': 0.9, 'supersedes_fact_id': str(facts['living_area']['id'])})
    corrected = Application(app.database.path).state()['sourcing']['comparable_screens'][0]
    assert corrected['items'][0]['decision'] == 'excluded_by_screen'
    assert corrected['subject_evidence_digest'] != original['subject_evidence_digest']
    app.withdraw_sale(sale['id'], {'reviewer': 'Synthetic', 'note': 'Wrong condition', 'evidence_reference': 'synthetic'})
    assert Application(app.database.path).state()['sourcing']['comparable_screens'] == []


def test_compressed_and_over_deadline_responses_are_rejected(monkeypatch):
    compressed = httpx.MockTransport(lambda r: httpx.Response(200, stream=httpx.ByteStream(b"{}"), headers={
        "Content-Type": "application/json", "Content-Encoding": "gzip"}))
    with pytest.raises(ValueError, match="uncompressed"):
        BoundedJSONTransport("https://example.com/fixed", transport=compressed).fetch({})
    clock = iter([0, 16])
    monkeypatch.setattr("app.provider_http.time.monotonic", lambda: next(clock))
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError, match="total time"):
        BoundedJSONTransport("https://example.com/fixed", transport=transport).fetch({})
