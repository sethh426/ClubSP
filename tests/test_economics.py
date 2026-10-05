from datetime import timedelta
import json
import threading

import pytest

from app.service import Application
from app.server import create_server
from core.memory.models import utc_now
from tests.test_sourcing import add_fact, accept_sale
from tests.test_finance_operations import fixture_deal, plan, state_deal
from tests.test_opportunities import policy, item
from tests.test_app import request


def prepared(tmp_path, strategy='assignment'):
    app, did = fixture_deal(tmp_path, strategy)
    pid = state_deal(app, did)['property_id']
    for k, v in {'parcel_id': '029999999999999999', 'property_type': 'single_family',
                 'property_class': 'Residential', 'neighborhood_code': '001',
                 'living_area': 1800.0, 'year_built': 1980.0, 'bath': 2.0, 'acreage': 0.2}.items():
        add_fact(app, pid, k, v)
    sale = accept_sale(app, pid, csv='Parcel Number,Address,Sale Date,Sale Price,Living Area,Property Class,Neighborhood Code,Year Built,Bath,Acreage\n028888888888888888,Synthetic comparable,' + utc_now().date().isoformat() + ',240000,1800,Residential,001,1980,2,0.2')
    app.underwrite(did, state_deal(app, did)['underwriting']['inputs'])
    plan(app, did, seller_price=80000, assignment_fee=20000 if strategy == 'assignment' else 0)
    policy(app, strategies=[strategy])
    return app, did, pid, sale


def screen(app, did):
    return item(app, did)['economic_screen']


def review_data(app, did, **changes):
    data = {k: 'Synthetic reviewed evidence' for k in ('reviewer', 'exit_price_reference', 'repair_reference',
        'funding_cost_reference', 'closing_selling_reference', 'owner_cost_partner_reference', 'condition_concessions_reference', 'note')}
    data.update(owner_confirmed_assumptions=True, context_digest=screen(app, did)['context_digest'])
    data.update(changes)
    return data


@pytest.mark.parametrize('strategy', ['assignment', 'resale'])
def test_owner_review_preserves_calculations_sources_and_restart(tmp_path, strategy):
    app, did, _, sale = prepared(tmp_path, strategy)
    original = screen(app, did)
    assert original['status'] == 'evidence_review_required'
    assert original['fit_comparable_count'] == 1
    forecasts = original['forecasts']
    review = app.review_economics(did, review_data(app, did))
    result = screen(Application(app.database.path), did)
    assert result['status'] == 'owner_reviewed'
    assert result['review']['id'] == review['id']
    assert result['review_current'] and not result['execution_authorized']
    assert result['forecasts'] == forecasts
    assert result['context']['fit_sale_ids'] == [sale['id']]
    assert result['assumptions']['partner_payout_allowance'] == 2000
    assert item(app, did)['decision'] == 'research'  # Missing owner, buyers and diligence remain separate.


@pytest.mark.parametrize('change', ['fact', 'sale', 'plan', 'underwriting', 'policy', 'expiry'])
def test_changed_context_and_expiry_invalidate_review(tmp_path, change):
    app, did, pid, sale = prepared(tmp_path)
    app.review_economics(did, review_data(app, did))
    if change == 'fact': add_fact(app, pid, 'roof_condition', 'Unreviewed change')
    if change == 'sale': app.withdraw_sale(sale['id'], {'reviewer': 'Synthetic', 'note': 'Invalid sale', 'evidence_reference': 'synthetic'})
    if change == 'plan': plan(app, did, seller_price=81000)
    if change == 'underwriting': app.underwrite(did, state_deal(app, did)['underwriting']['inputs'])
    if change == 'policy': policy(app, max_seller_price=140000)
    if change == 'expiry':
        with app.database.session(write=True) as (connection, _):
            row = connection.execute('SELECT id,body FROM economic_reviews').fetchone()
            body = json.loads(row['body']); body['created_at'] = (utc_now() - timedelta(days=31)).isoformat()
            connection.execute('UPDATE economic_reviews SET body=? WHERE id=?', (json.dumps(body), row['id']))
    result = screen(app, did)
    assert not result['review_current']
    assert result['status'] != 'owner_reviewed'
    assert result['review']


@pytest.mark.parametrize('changes', [{'owner_confirmed_assumptions': False}, {'context_digest': 'old'},
    {'repair_reference': ''}, {'funding_cost_reference': ''}, {'owner_cost_partner_reference': ''}])
def test_incomplete_or_racing_review_is_atomic(tmp_path, changes):
    app, did, _, _ = prepared(tmp_path)
    with pytest.raises(ValueError): app.review_economics(did, review_data(app, did, **changes))
    with app.database.session() as (connection, _):
        assert connection.execute('SELECT count(*) FROM economic_reviews').fetchone()[0] == 0


@pytest.mark.parametrize('reason', ['downside', 'missing_comps', 'cash', 'buy_box'])
def test_review_cannot_override_failed_limits_or_missing_evidence(tmp_path, reason):
    app, did, pid, sale = prepared(tmp_path)
    if reason == 'downside': plan(app, did)
    if reason == 'missing_comps': app.withdraw_sale(sale['id'], {'reviewer': 'Synthetic', 'note': 'Invalid', 'evidence_reference': 'synthetic'})
    if reason == 'cash': policy(app, max_deal_cash_at_risk=5000)
    if reason == 'buy_box': policy(app, markets=['Indianapolis, IN'])
    result = screen(app, did)
    assert not result['review_allowed']
    with pytest.raises(ValueError, match='Resolve economic'):
        app.review_economics(did, review_data(app, did))


def test_http_review_and_origin_protection(tmp_path):
    app, did, _, _ = prepared(tmp_path)
    server = create_server(app.database.path, port=0, application=app)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    try:
        code, body, _ = request(base, '/api/deals/' + did + '/economic-review', review_data(app, did), headers={'Origin': 'https://foreign.example'})
        assert code == 403
        code, body, _ = request(base, '/api/deals/' + did + '/economic-review', review_data(app, did))
        assert code == 201
        assert json.loads(body)['context_digest'] == screen(app, did)['context_digest']
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=5)


def test_review_retries_are_idempotent_and_portfolio_changes_require_refresh(tmp_path):
    app, did, pid, _ = prepared(tmp_path)
    data = review_data(app, did)
    first = app.review_economics(did, data)
    assert app.review_economics(did, data)["id"] == first["id"]
    other = app.create_deal({"property_id": pid, "strategy": "assignment"})
    result = screen(app, did)
    assert not result["review_current"]
    assert any("portfolio" in gap for gap in result["gaps"])
    with app.database.session() as (connection, _):
        assert connection.execute("SELECT count(*) FROM economic_reviews").fetchone()[0] == 1


def test_review_does_not_hide_portfolio_risk(tmp_path):
    app, did, _, _ = prepared(tmp_path)
    policy(app, max_deal_cash_at_risk=5000, max_portfolio_cash_at_risk=5000)
    assert any("Portfolio cash exposure" in reason for reason in screen(app, did)["failed_limits"])


def test_all_accepted_comps_must_fit_before_review(tmp_path):
    app, did, pid, _ = prepared(tmp_path)
    extra = accept_sale(app, pid, csv='Parcel Number,Address,Sale Date,Sale Price,Living Area\n027777777777777777,Synthetic incomplete,' + utc_now().date().isoformat() + ',230000,1800')
    app.underwrite(did, state_deal(app, did)['underwriting']['inputs'])
    plan(app, did, seller_price=80000)
    result = screen(app, did)
    assert result['fit_comparable_count'] == 1
    assert not result['review_allowed']
    assert any('Correct or withdraw' in gap for gap in result['gaps'])
    app.withdraw_sale(extra['id'], {'reviewer': 'Synthetic', 'note': 'Unusable', 'evidence_reference': 'Synthetic'})
    app.underwrite(did, state_deal(app, did)['underwriting']['inputs'])
    plan(app, did, seller_price=80000)
    assert screen(app, did)['review_allowed']


def test_ended_deal_cannot_receive_review(tmp_path):
    app, did, _, _ = prepared(tmp_path)
    data = review_data(app, did)
    app.advance_deal(did, {'stage': 'lost', 'note': 'Synthetic loss'})
    with pytest.raises(ValueError, match='pre-contract'):
        app.review_economics(did, data)


def test_additive_migration_preserves_existing_deals_and_evidence(tmp_path):
    app, did, _, _ = prepared(tmp_path)
    before = app.state()
    with app.database.session(write=True) as (connection, _):
        connection.execute('DROP TABLE economic_reviews')
    restarted = Application(app.database.path)
    after = restarted.state()
    assert after['properties'] == before['properties']
    assert after['facts'] == before['facts']
    assert after['deals'] == before['deals']
    with restarted.database.session() as (connection, _):
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert connection.execute('SELECT count(*) FROM economic_reviews').fetchone()[0] == 0
