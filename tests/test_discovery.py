from datetime import datetime, timezone
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from app.discovery import parse_notice
from app.service import Application
from app.server import create_server

NOTICE = '''<h1>Sale of North Campus Property</h1><p>Synthetic property located at 123 Example Road, Fort Wayne, Indiana.
Tax Parcel No. 02-01-01-101-001.000-001.
No bid for less than Example Dollars ($100,000.00) shall be considered.
The bid period begins October 1, 2026, at 11 AM eastern time through November 30, 2026 at 11 AM eastern time.</p>
<script>fake located at 999 Bad Road, Fort Wayne, Indiana</script>'''


SHERIFF_TEXT = """[[SOURCE_DOCUMENT:https://www.allencountysheriff.org/wp-content/uploads/2026/09/OCTOBER-2026-1.pdf]]
1 DATE OF SALE CAUSE NUMBER ADDRESS CANCELLATION DATE JUDGEMENT BID ATTORNEY PHONE SOLD TO SOLD FOR SATISFIED SHERIFF FEES
2 10/21/2026 02D03-2505-MF-000199 3817 MARIGOLD DR FORT WAYNE, IN 46815 $ 72,048.49 ELYSSA MEADE 850-422-2520 $ 313.00
3 10/21/2026 02D03-2512-MF-000505 4614 GOLFVIEW DR FORT WAYNE, IN 46818 9/4/2026 $ 132,877.46 BRYAN REDMOND 317-237-2727 CANCELLED 9/4/26 $ 313.00
4 10/21/2026 02D03-2512-MF-000514 1201 HANCOCK AVE FORT WAYNE. IN 46803 $ 115,905.46 WESLEY PAGLES 513-396-8100 $ 319.00
[[SOURCE_DOCUMENT:https://www.allencountysheriff.org/wp-content/uploads/2026/09/NOVEMBER-2026.pdf]]
NO SALES FOR THE MONTH OF NOVEMBER
"""


def test_notice_fields_and_time_windows():
    result = parse_notice('north_campus', NOTICE, datetime(2026, 10, 2, tzinfo=timezone.utc))
    assert result['status'] == 'advertised_window'
    assert result['candidates'][0]['address'] == '123 Example Road'
    assert result['candidates'][0]['minimum_bid'] == 100000
    assert result['candidates'][0]['bid_start'].endswith('-04:00')
    assert parse_notice('north_campus', NOTICE.replace('2026, at', '2026 at'), datetime(2026, 12, 1, tzinfo=timezone.utc))['status'] == 'past_advertised_window'
    assert parse_notice('north_campus', NOTICE, datetime(2026, 9, 1, tzinfo=timezone.utc))['status'] == 'before_advertised_window'
    with pytest.raises(ValueError): parse_notice('north_campus', NOTICE.replace('$100,000.00', 'unknown'), datetime.now(timezone.utc))


def test_empty_inventory_and_unknown_layout():
    now = datetime.now(timezone.utc)
    assert parse_notice('accdc', '<h1>ACCDC Properties</h1>Currently there are no properties available.', now)['status'] == 'no_inventory'
    assert parse_notice('accdc', '<h1>ACCDC Properties</h1>Contact us', now)['status'] == 'needs_review'
    with pytest.raises(ValueError): parse_notice('accdc', 'login required', now)


def test_cache_persistence_and_no_deals(tmp_path):
    app = Application(tmp_path/'app.db'); calls = []
    app.discovery_fetch = lambda url: calls.append(url) or NOTICE
    first = app.check_discovery({'source_id': 'north_campus'})
    second = app.check_discovery({'source_id': 'north_campus'})
    assert first['id'] == second['id'] and second['cached'] and len(calls) == 1
    assert not app.state()['properties'] and not app.state()['deals']
    assert Application(tmp_path/'app.db').discovery_state()['sources'][1]['candidates'][0]['minimum_bid'] == 100000


def test_failure_budget_and_source_allowlist(tmp_path):
    app = Application(tmp_path/'app.db'); calls = []
    def fail(url):
        calls.append(url); raise OSError('sensitive raw failure')
    app.discovery_fetch = fail
    for _ in range(6):
        result = app.check_discovery({'source_id': 'accdc'})
        assert result['status'] == 'failed' and 'sensitive' not in json.dumps(result)
    with pytest.raises(ValueError, match='budget'): app.check_discovery({'source_id': 'accdc'})
    with pytest.raises(ValueError): app.check_discovery({'source_id': 'https://evil.example'})
    with pytest.raises(ValueError): app.check_discovery({'source_id': 'accdc', 'url': 'https://evil.example'})
    assert len(calls) == 6
    app.discovery_lock.acquire()
    with pytest.raises(ValueError, match='running'): app.check_discovery({'source_id': 'north_campus'})
    app.discovery_lock.release()


def test_origin_required_and_no_network_on_get(tmp_path):
    app = Application(tmp_path/'app.db'); calls = []
    app.discovery_fetch = lambda url: calls.append(url) or NOTICE
    server = create_server(tmp_path/'app.db', port=0, application=app)
    origin = 'http://127.0.0.1:' + str(server.server_address[1])
    worker = threading.Thread(target=server.serve_forever); worker.start()
    try:
        with urlopen(origin+'/api/discovery') as response: assert json.load(response)['automatic_checks'] is False
        assert not calls
        with pytest.raises(HTTPError) as rejected:
            urlopen(Request(origin+'/api/discovery/check', data=b'{"source_id":"north_campus"}', headers={'Content-Type':'application/json'}))
        assert rejected.value.code == 403 and not calls
        with urlopen(Request(origin+'/api/discovery/check', data=b'{"source_id":"north_campus"}', headers={'Content-Type':'application/json','Origin':origin})) as response:
            assert json.load(response)['candidates'][0]['minimum_bid'] == 100000
    finally:
        server.shutdown(); server.server_close(); worker.join()

def test_saved_notice_expiry_is_independent_of_purchase_policy(tmp_path):
    app = Application(tmp_path/'app.db')
    app.discovery_fetch = lambda url: NOTICE
    app.check_discovery({'source_id': 'north_campus'})
    def candidate():
        return app.discovery_state()['sources'][1]['candidates'][0]
    assert 'within_recorded_price_limit' not in candidate()
    with app.database.session(write=True) as (connection, _):
        connection.execute("INSERT INTO opportunity_policies (id, created_at, body) VALUES (?, ?, ?)", ('price-test', datetime.now(timezone.utc).isoformat(), json.dumps({'max_seller_price': 50000})))
    assert not any('price limit' in blocker for blocker in candidate()['intake_blockers'])
    with app.database.session(write=True) as (connection, _):
        connection.execute("UPDATE opportunity_policies SET body=?", (json.dumps({'max_seller_price': 150000}),))
        row = connection.execute("SELECT id, body FROM discovery_checks").fetchone()
        body = json.loads(row['body']); body['fetched_at'] = '2000-01-01T00:00:00+00:00'
        body['candidates'][0]['bid_end'] = '2000-01-02T00:00:00+00:00'
        connection.execute("UPDATE discovery_checks SET body=? WHERE id=?", (json.dumps(body), row['id']))
    saved = app.discovery_state()['sources'][1]
    assert saved['stale']
    assert 'within_recorded_price_limit' not in candidate()
    assert any('ended' in gap for gap in candidate()['review_gaps'])


def test_http_forbidden_is_recorded_as_source_access_blocked(tmp_path):
    app = Application(tmp_path/'app.db')
    def forbidden(url):
        raise HTTPError(url, 403, 'Forbidden', {}, None)
    app.discovery_fetch = forbidden
    result = app.check_discovery({'source_id': 'north_campus'})
    assert result['status'] == 'source_access_blocked'
    assert result['retrieval_mode'] == 'server_fetch'
    assert result['candidates'] == []
    assert 'http_status' not in result
    assert 'error' not in result
    assert 'refused this server-side request' in result['excerpt']
    cached = app.check_discovery({'source_id': 'north_campus'})
    assert cached['cached'] is True and cached['id'] == result['id']


def test_reviewed_official_snapshot_parses_persists_and_creates_no_deal(tmp_path):
    app = Application(tmp_path/'app.db')
    result = app.record_discovery_snapshot({
        'source_id': 'north_campus',
        'source_url': 'https://www.allencounty.in.gov/1305/Sale-of-North-Campus-Property',
        'body': NOTICE,
        'reviewer': 'Owner review',
        'note': 'Copied from the configured official Allen County page.',
    })
    assert result['retrieval_mode'] == 'reviewed_official_snapshot'
    assert result['status'] == 'advertised_window'
    assert result['candidates'][0]['minimum_bid'] == 100000
    state = app.discovery_state()['sources'][1]
    assert state['id'] == result['id']
    assert state['reviewer'] == 'Owner review'
    assert not app.state()['properties'] and not app.state()['deals']


@pytest.mark.parametrize('change', [
    {'source_url': 'https://evil.example'},
    {'body': ''},
    {'reviewer': ''},
    {'note': ''},
])
def test_reviewed_snapshot_rejects_bad_provenance_atomically(tmp_path, change):
    app = Application(tmp_path/'app.db')
    data = {
        'source_id': 'north_campus',
        'source_url': 'https://www.allencounty.in.gov/1305/Sale-of-North-Campus-Property',
        'body': NOTICE,
        'reviewer': 'Owner review',
        'note': 'Verified official page.',
    }
    data.update(change)
    before = len(app.discovery_state()['sources'][1].get('candidates', []))
    with pytest.raises(ValueError):
        app.record_discovery_snapshot(data)
    after = len(app.discovery_state()['sources'][1].get('candidates', []))
    assert after == before


def test_reviewed_snapshot_http_route_requires_origin_and_persists(tmp_path):
    app = Application(tmp_path/'app.db')
    server = create_server(tmp_path/'app.db', port=0, application=app)
    origin = 'http://127.0.0.1:' + str(server.server_address[1])
    worker = threading.Thread(target=server.serve_forever); worker.start()
    payload = {
        'source_id': 'north_campus',
        'source_url': 'https://www.allencounty.in.gov/1305/Sale-of-North-Campus-Property',
        'body': NOTICE,
        'reviewer': 'Owner review',
        'note': 'Reviewed configured official page.',
    }
    try:
        rejected = Request(
            origin+'/api/discovery/snapshot',
            data=json.dumps(payload).encode(),
            headers={'Content-Type':'application/json'},
            method='POST',
        )
        with pytest.raises(HTTPError) as error:
            urlopen(rejected)
        assert error.value.code == 403

        accepted = Request(
            origin+'/api/discovery/snapshot',
            data=json.dumps(payload).encode(),
            headers={'Content-Type':'application/json','Origin':origin},
            method='POST',
        )
        with urlopen(accepted) as response:
            saved = json.load(response)
        assert saved['retrieval_mode'] == 'reviewed_official_snapshot'
        assert saved['candidates'][0]['minimum_bid'] == 100000
        assert app.discovery_state()['sources'][1]['id'] == saved['id']
        assert not app.state()['properties'] and not app.state()['deals']
    finally:
        server.shutdown(); server.server_close(); worker.join()


def test_sheriff_sale_parser_excludes_cancelled_and_never_invents_purchase_price():
    result = parse_notice('sheriff_sales', SHERIFF_TEXT, datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert result['status'] == 'scheduled_sales'
    assert [c['address'] for c in result['candidates']] == ['1201 HANCOCK AVE', '3817 MARIGOLD DR']
    candidate = result['candidates'][1]
    assert candidate['cause_number'] == '02D03-2505-MF-000199'
    assert candidate['sale_date'] == '2026-10-21'
    assert candidate['judgment_amount'] == 72048.49
    assert candidate['minimum_bid'] is None
    assert candidate['intake_supported'] is False
    assert candidate['parcel_ids'] == []
    assert 'not a purchase price' in candidate['price_basis']
    assert candidate['source_document_url'].endswith('OCTOBER-2026-1.pdf')
    assert '1 cancelled row(s) excluded' in result['excerpt']


def test_sheriff_sale_no_sales_and_unknown_layout():
    now = datetime(2026, 11, 5, tzinfo=timezone.utc)
    no_sales = '[[SOURCE_DOCUMENT:https://example.test/november.pdf]]\nNO SALES FOR THE MONTH OF NOVEMBER'
    result = parse_notice('sheriff_sales', no_sales, now)
    assert result['status'] == 'no_inventory' and result['candidates'] == []
    with pytest.raises(ValueError, match='layout changed'):
        parse_notice('sheriff_sales', 'unrelated document', now)


def test_sheriff_discovery_persists_research_only_candidates_and_unknown_price_buyer_screen(tmp_path):
    app = Application(tmp_path/'app.db')
    app.sheriff_discovery_fetch = lambda url, now: SHERIFF_TEXT
    app.create_buyer({
        'name': 'Synthetic buyer', 'company': 'Synthetic',
        'locations': ['fort wayne, in'], 'strategies': ['assignment'],
        'property_types': [], 'max_total_price': 1, 'max_repairs': 0,
        'funding_status': 'unverified', 'verified_at': '', 'verification_reference': '',
    })
    saved = app.check_discovery({'source_id': 'sheriff_sales'})
    assert saved['status'] == 'scheduled_sales'
    state = app.discovery_state()
    sheriff = next(item for item in state['sources'] if item['source_id'] == 'sheriff_sales')
    candidate = sheriff['candidates'][0]
    assert candidate['intake_supported'] is False
    assert any('parcel identity' in blocker.lower() for blocker in candidate['intake_blockers'])
    assert candidate['buyer_criteria'][0]['status'] == 'needs_more_information'
    assert any('No acquisition price is established' in reason for reason in candidate['buyer_criteria'][0]['reasons'])
    assert not any('exceeds' in reason for reason in candidate['buyer_criteria'][0]['reasons'])
    assert not app.state()['properties'] and not app.state()['deals']
    again = app.check_discovery({'source_id': 'sheriff_sales'})
    assert again['cached'] is True and again['id'] == saved['id']
