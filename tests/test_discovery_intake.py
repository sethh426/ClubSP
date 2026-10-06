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

def test_research_has_identity_gates_but_no_purchase_budget_gate(notice_app):
    app, notice = notice_app; data = payload(notice)
    for changed in [dict(identity_confirmed=False), dict(parcel_id='not-in-notice'), dict(candidate_index=True),
                    dict(zip='bad'), dict(address='forged')]:
        with pytest.raises(ValueError): app.stage_discovery_intake({**data, **changed})
    app.save_opportunity_policy({**POLICY, 'max_seller_price':50000})
    assert app.stage_discovery_intake(data)['status'] == 'pending'
    assert not app.state()['properties'] and not app.state()['deals']

def test_acceptance_rechecks_staleness_without_financial_policy_gate(notice_app, monkeypatch):
    app, notice = notice_app; staged = app.stage_discovery_intake(payload(notice))
    app.save_opportunity_policy({**POLICY, 'markets':['Other city, IN']})
    assert app.discovery_state()['sources'][1]['candidates'][0]['intake_blockers'] == []
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

def test_no_budget_required_but_future_notice_is_blocked(notice_app, monkeypatch):
    app, notice = notice_app
    with app.database.session(write=True) as (connection, _):
        connection.execute('DELETE FROM opportunity_policies')
    assert app.stage_discovery_intake(payload(notice))['status'] == 'pending'
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

def test_identity_acceptance_without_purchase_budget(notice_app):
    app, notice = notice_app
    with app.database.session(write=True) as (connection, _):
        connection.execute('DELETE FROM opportunity_policies')
    staged = app.stage_discovery_intake(payload(notice))
    accepted = app.review_candidate(staged['row_id'], REVIEW)
    assert accepted['property_id'] and not app.state()['deals']

def test_preliminary_buyer_criteria_do_not_claim_funding_or_block_research(notice_app):
    app, notice = notice_app
    common = dict(company='Synthetic', strategies=['assignment'], property_types=['land'],
                  max_repairs=10000, funding_status='unverified')
    buyer = app.create_buyer(dict(common, name='Synthetic market buyer', locations=['Fort Wayne, IN'], max_total_price=150000))
    app.create_buyer(dict(common, name='Synthetic other buyer', locations=['Other city, IN'], max_total_price=50000))
    candidate = app.discovery_state()['sources'][1]['candidates'][0]
    profiles = {m['name']:m for m in candidate['buyer_criteria']}
    assert profiles['Synthetic market buyer']['status'] == 'needs_more_information'
    assert profiles['Synthetic other buyer']['status'] == 'outside_recorded_criteria'
    assert all(m['commitment_confirmed'] is False for m in profiles.values())
    assert candidate['intake_blockers'] == []
    assert app.stage_discovery_intake(payload(notice))['status'] == 'pending'
    with app.database.session() as (connection, _):
        matches = intake.preliminary_notice_buyers(connection, notice['candidates'][0], 'land')
    assert next(m for m in matches if m['buyer_id'] == buyer['id'])['status'] == 'possible_fit_on_known_fields'


def sheriff_resolved_notice():
    return {
        "id": "sheriff-resolved-check",
        "source_id": "sheriff_sales",
        "name": "Allen County Sheriff mortgage foreclosure sales",
        "url": "https://www.allencountysheriff.org/2026-sheriff-sales/",
        "fetched_at": NOW.isoformat(),
        "status": "scheduled_sales",
        "content_hash": "synthetic-sheriff-hash",
        "candidates": [{
            "address": "100 GIS REVIEW AVE",
            "city": "Fort Wayne", "state": "IN", "zip": "46805",
            "cause_number": "02D03-2601-MF-000001",
            "sale_date": "2026-10-21",
            "judgment_amount": 90000.0,
            "minimum_bid": None,
            "parcel_ids": ["02-00-00-000-001.000-000"],
            "intake_supported": True,
            "source_document_url": "https://www.allencountysheriff.org/example.pdf",
            "identity_note": "Parcel identity resolved against Allen County GIS.",
            "availability": "Synthetic scheduled sheriff notice.",
            "parcel_resolution": {
                "status": "resolved",
                "pin": "020000000001000000",
                "gis_id": "02-00-00-000-001.000-000",
                "official_address": "100 GIS REVIEW AVE",
                "zip": "46805",
                "municipality": "FW",
                "property_class": "1 Family Dwell - Platted Lot",
                "assessed_total": 185000,
                "prior_sale_price": 140000,
                "prior_sale_date": 1640995200000,
                "year_built": 1955,
                "legal_acreage": 0.22,
                "site_address_service": "https://gis.acimap.us/acfw/rest/services/Parcels/SiteAddresses_TrimbleUnity/FeatureServer/0",
                "parcel_service": "https://gis.acimap.us/acfw/rest/services/Parcels/AC_Parcel_iMap_org/FeatureServer/20",
                "checked_at": NOW.isoformat(),
            },
        }],
    }


def stage_resolved_sheriff(app):
    notice = sheriff_resolved_notice()
    with app.database.session(write=True) as (connection, _):
        connection.execute(
            "INSERT INTO discovery_checks VALUES(?,?,?,?)",
            (notice["id"], notice["source_id"], notice["fetched_at"], json.dumps(notice)),
        )
    return app.stage_discovery_intake({
        "check_id": notice["id"], "candidate_index": 0,
        "parcel_id": notice["candidates"][0]["parcel_ids"][0],
        "zip": "46805", "property_type": "single_family",
        "reviewer": "Synthetic reviewer",
        "note": "Synthetic official GIS identity reviewed",
        "identity_confirmed": True,
    })


def test_accepted_discovery_property_preserves_saved_official_gis_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(intake, "utc_now", lambda: NOW)
    app = Application(tmp_path / "app.db")
    staged = stage_resolved_sheriff(app)
    accepted = app.review_candidate(staged["row_id"], REVIEW)
    pid = accepted["property_id"]
    state = app.state()
    facts = {
        f["attribute"]: f
        for f in state["facts"]
        if str(f["subject_id"]) == pid
    }
    assert facts["parcel_id"]["value"] == "02-00-00-000-001.000-000"
    assert facts["property_type"]["value"] == "single_family"
    assert facts["official_gis_address"]["value"] == "100 GIS REVIEW AVE"
    assert facts["property_class"]["value"] == "1 Family Dwell - Platted Lot"
    assert facts["assessed_total"]["value"] == 185000
    assert facts["prior_sale_price"]["value"] == 140000
    assert facts["prior_sale_date"]["value"] == "2022-01-01"
    assert facts["year_built"]["value"] == 1955
    assert facts["legal_acreage"]["value"] == 0.22
    gis_source = next(
        source for source in state["sources"]
        if source["id"] == facts["assessed_total"]["source_id"]
    )
    assert gis_source["provider"] == "Allen County GIS"
    assert gis_source["source_type"] == "official_gis"
    assert "FeatureServer/20" in gis_source["url"]
    assert not state["deals"]

    before_fact_count = len(state["facts"])
    before_source_count = len(state["sources"])
    first = app.refresh_discovery_gis_evidence(staged["row_id"], {})
    second = app.refresh_discovery_gis_evidence(staged["row_id"], {})
    assert first["gis_evidence_attributes"] == second["gis_evidence_attributes"]
    restarted = Application(app.database.path).state()
    assert len(restarted["facts"]) == before_fact_count
    assert len(restarted["sources"]) == before_source_count
    assert not restarted["deals"]


def test_gis_backfill_rejects_pending_or_non_discovery_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(intake, "utc_now", lambda: NOW)
    app = Application(tmp_path / "app.db")
    staged = stage_resolved_sheriff(app)
    with pytest.raises(ValueError, match="accepted"):
        app.refresh_discovery_gis_evidence(staged["row_id"], {})
    app.review_candidate(staged["row_id"], REVIEW)
    with pytest.raises(ValueError, match="editable fields"):
        app.refresh_discovery_gis_evidence(staged["row_id"], {"forged": True})


def test_gis_backfill_http_route_updates_accepted_discovery_row(tmp_path, monkeypatch):
    from threading import Thread
    from urllib.request import Request, urlopen
    from app.server import create_server

    monkeypatch.setattr(intake, "utc_now", lambda: NOW)
    app = Application(tmp_path / "app.db")
    staged = stage_resolved_sheriff(app)
    app.review_candidate(staged["row_id"], REVIEW)

    server = create_server(app.database.path, port=0, application=app)
    origin = "http://127.0.0.1:" + str(server.server_address[1])
    worker = Thread(target=server.serve_forever); worker.start()
    try:
        request = Request(
            origin + "/api/sourcing/rows/" + staged["row_id"] + "/refresh-gis",
            data=b"{}",
            headers={"Content-Type": "application/json", "Origin": origin},
            method="POST",
        )
        with urlopen(request) as response:
            saved = json.load(response)
        assert saved["property_id"]
        assert "assessed_total" in saved["gis_evidence_attributes"]
        assert saved["execution_authorized"] is False
        assert not app.state()["deals"]
    finally:
        server.shutdown(); server.server_close(); worker.join()
