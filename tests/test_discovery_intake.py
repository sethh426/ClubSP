from datetime import datetime, timedelta, timezone
import json
import pytest
from app.service import Application
from tests.test_discovery import NOTICE
import app.discovery_intake as intake

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
POLICY = dict(markets=['Fort Wayne, IN'], strategies=['resale'], property_types=['land'],
              max_seller_price=150000, max_deal_cash_at_risk=150000,
              max_portfolio_cash_at_risk=300000, min_downside_net=1000,
              evidence_max_age_days=30, basis='Synthetic test policy')
REVIEW = dict(action='accept', identity_confirmed=True, reviewer='Synthetic reviewer',
              note='Synthetic survey identity reviewed', evidence_reference='test:survey')

@pytest.fixture
def notice_app(tmp_path, monkeypatch):
    monkeypatch.setattr(intake, 'utc_now', lambda: NOW)
    app = Application(tmp_path/'app.db'); app.discovery_fetch = lambda url: NOTICE
    notice = app.check_discovery({'source_id':'north_campus'})
    notice['fetched_at'] = NOW.isoformat()
    with app.database.session(write=True) as (connection, _):
        connection.execute('UPDATE discovery_checks SET fetched_at=?,body=? WHERE id=?',
                           (notice['fetched_at'], json.dumps(notice), notice['id']))
    app.save_opportunity_policy(POLICY)
    return app, notice

def payload(notice):
    return dict(check_id=notice['id'], candidate_index=0, parcel_id=notice['candidates'][0]['parcel_ids'][0],
                zip='46818', property_type='land', reviewer='Synthetic reviewer',
                note='Synthetic survey and offered parcel portions checked', identity_confirmed=True)

def test_stage_then_existing_identity_review(notice_app):
    app, notice = notice_app; data = payload(notice)
    staged = app.stage_discovery_intake(data)
    state = app.state()
    assert not state['properties'] and not state['deals']
    batch = state['sourcing']['batches'][0]
    assert batch['discovery']['candidate']['minimum_bid'] == 100000
    assert batch['discovery']['notice']['content_hash'] == notice['content_hash']
    assert app.stage_discovery_intake(data)['id'] == staged['id']
    assert len(app.state()['sourcing']['rows']) == 1
    accepted = app.review_candidate(staged['row_id'], REVIEW)
    assert accepted['property_id'] and len(app.state()['properties']) == 1
    assert not app.state()['deals']
    assert not any(f['attribute'] == 'seller_price' for f in app.state()['facts'])

def test_policy_and_identity_gates(notice_app):
    app, notice = notice_app; data = payload(notice)
    for changed in [dict(identity_confirmed=False), dict(parcel_id='not-in-notice'), dict(candidate_index=True),
                    dict(zip='bad'), dict(property_type='condo'), dict(address='forged')]:
        with pytest.raises(ValueError): app.stage_discovery_intake({**data, **changed})
    app.save_opportunity_policy({**POLICY, 'max_seller_price':50000})
    with pytest.raises(ValueError, match='minimum bid'): app.stage_discovery_intake(data)
    assert not app.state()['sourcing']['rows']

def test_acceptance_rechecks_policy_and_staleness(notice_app, monkeypatch):
    app, notice = notice_app; staged = app.stage_discovery_intake(payload(notice))
    app.save_opportunity_policy({**POLICY, 'markets':['Other city, IN']})
    with pytest.raises(ValueError, match='market'): app.review_candidate(staged['row_id'], REVIEW)
    app.save_opportunity_policy(POLICY)
    monkeypatch.setattr(intake, 'utc_now', lambda: NOW + timedelta(days=2))
    with pytest.raises(ValueError, match='stale'): app.review_candidate(staged['row_id'], REVIEW)
    assert not app.state()['properties'] and app.state()['sourcing']['rows'][0]['status'] == 'pending'

def test_newer_check_requires_restaging_and_refreshes_pending_review(notice_app):
    app, notice = notice_app; staged = app.stage_discovery_intake(payload(notice))
    new = {**notice, 'id':'synthetic-new-check'}
    with app.database.session(write=True) as (connection, _):
        connection.execute('INSERT INTO discovery_checks VALUES(?,?,?,?)',
                           (new['id'], new['source_id'], new['fetched_at'], json.dumps(new)))
    with pytest.raises(ValueError, match='latest'): app.review_candidate(staged['row_id'], REVIEW)
    updated = app.stage_discovery_intake(payload(new))
    assert updated['duplicate'] and updated['row_id'] == staged['row_id']
    assert app.state()['sourcing']['batches'][0]['discovery']['check_id'] == new['id']
    assert app.review_candidate(staged['row_id'], REVIEW)['property_id']

def test_before_window_and_missing_policy_are_blocked(notice_app, monkeypatch):
    app, notice = notice_app
    with app.database.session(write=True) as (connection, _):
        connection.execute('DELETE FROM opportunity_policies')
    with pytest.raises(ValueError, match='buy box'): app.stage_discovery_intake(payload(notice))
    app.save_opportunity_policy(POLICY)
    monkeypatch.setattr(intake, 'utc_now', lambda: datetime(2026, 9, 30, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match='bid window'): app.stage_discovery_intake(payload(notice))

def test_intake_endpoint_requires_same_origin(notice_app):
    from threading import Thread
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    from app.server import create_server
    app, notice = notice_app
    server = create_server(app.database.path, port=0, application=app)
    origin = 'http://127.0.0.1:' + str(server.server_address[1])
    worker = Thread(target=server.serve_forever); worker.start()
    try:
        raw = json.dumps(payload(notice)).encode()
        with pytest.raises(HTTPError) as rejected:
            urlopen(Request(origin+'/api/discovery/intake', data=raw, headers={'Content-Type':'application/json'}))
        assert rejected.value.code == 403 and not app.state()['sourcing']['rows']
        with urlopen(Request(origin+'/api/discovery/intake', data=raw, headers={'Content-Type':'application/json','Origin':origin})) as response:
            assert json.load(response)['status'] == 'pending'
        assert not app.state()['properties'] and not app.state()['deals']
    finally:
        server.shutdown(); server.server_close(); worker.join()


def test_existing_parcel_is_detected_across_display_formats(notice_app):
    from core.memory import Fact, SourceRecord
    from uuid import UUID
    app, notice = notice_app
    staged = app.stage_discovery_intake(payload(notice))
    existing = app.create_property(dict(address='Different address spelling', city='Fort Wayne', state='IN', zip='46818'))
    with app.database.session(write=True) as (connection, memory):
        source = memory.add_source(SourceRecord(source_type='synthetic', provider='Test fixture'))
        memory.add_fact(Fact(subject_type='property', subject_id=UUID(existing['id']), source_id=source.id, attribute='parcel_id',
                             value=notice['candidates'][0]['parcel_ids'][0].replace('-', '').replace('.', ''), value_type='text', confidence=0.5))
    with pytest.raises(ValueError, match='parcel'): app.review_candidate(staged['row_id'], REVIEW)
    assert len(app.state()['properties']) == 1
