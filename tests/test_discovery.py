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

def test_saved_notice_rechecks_price_limit_and_expiry(tmp_path):
    app = Application(tmp_path/'app.db')
    app.discovery_fetch = lambda url: NOTICE
    app.check_discovery({'source_id': 'north_campus'})
    def candidate():
        return app.discovery_state()['sources'][1]['candidates'][0]
    assert candidate()['within_recorded_price_limit'] is None
    with app.database.session(write=True) as (connection, _):
        connection.execute("INSERT INTO opportunity_policies (id, created_at, body) VALUES (?, ?, ?)", ('price-test', datetime.now(timezone.utc).isoformat(), json.dumps({'max_seller_price': 50000})))
    assert candidate()['within_recorded_price_limit'] is False
    with app.database.session(write=True) as (connection, _):
        connection.execute("UPDATE opportunity_policies SET body=?", (json.dumps({'max_seller_price': 150000}),))
        row = connection.execute("SELECT id, body FROM discovery_checks").fetchone()
        body = json.loads(row['body']); body['fetched_at'] = '2000-01-01T00:00:00+00:00'
        body['candidates'][0]['bid_end'] = '2000-01-02T00:00:00+00:00'
        connection.execute("UPDATE discovery_checks SET body=? WHERE id=?", (json.dumps(body), row['id']))
    saved = app.discovery_state()['sources'][1]
    assert saved['stale'] and candidate()['within_recorded_price_limit'] is True
    assert any('ended' in gap for gap in candidate()['review_gaps'])
