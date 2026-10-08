from datetime import datetime, timedelta, timezone
import json
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from app.buyer_intent import BuyerIntentBook, BuyerIntentScheduler, SOURCES, classify
from app.relationships import RelationshipBook
from app.server import create_server
from app.service import Application


@pytest.fixture
def book(tmp_path):
    app = Application(tmp_path / 'app.db')
    book = BuyerIntentBook(app, RelationshipBook(app))
    book.fetch_posts = lambda source: []  # All tests are offline, including the new feed.
    return book


def finding(**extra):
    return {'name': 'Synthetic Buyer', 'url': 'https://example.com/posts/123',
            'kind': 'post', 'text': "I'm buying houses in Fort Wayne under $150,000.", **extra}


def test_unknown_dates_old_posts_solicitations_and_history(book):
    book.capture(finding())
    assert book.state()['signals'][0]['freshness'] == 'Date unknown'
    assert book.state()['signals'][0]['priority'] == 1
    day = (datetime.now(timezone.utc) - timedelta(days=31)).date().isoformat()
    book.capture(finding(published_on=day))
    row = book.state()['signals'][0]
    assert row['freshness'] == 'Older than 30 days'
    assert len(row['history']) == 2
    book.capture(finding(url='https://example.com/network', text='Looking for cash buyers in Indiana. We have deals.'))
    row = next(s for s in book.state()['signals'] if s['url'].endswith('network'))
    assert row['category'] == 'buyer_solicitation'
    with pytest.raises(ValueError, match='Only an undismissed'):
        book.relationship(row['id'], {})
    book.capture(finding(url='https://example.com/bought', kind='acquisition'))
    assert next(s for s in book.state()['signals'] if s['url'].endswith('bought'))['category'] == 'acquisition_history'


def test_url_dedupe_tracking_and_real_query_identity(book):
    first = book.capture(finding(url='https://example.com/posts/123/?utm_source=test#comments'))
    assert book.capture(finding()) == first
    assert len(book.state()['signals']) == 1
    assert len(book.state()['signals'][0]['history']) == 1
    book.capture(finding(url='https://example.com/posts/123?comment=other'))
    assert len(book.state()['signals']) == 2


@pytest.mark.parametrize('extra', [{'url':'javascript:alert(1)'}, {'url':'https://user:pass@example.com/'},
                                  {'url':'http://example.com/'}, {'published_on':'2999-01-01'},
                                  {'published_on':'2026-99-01'}, {'kind':'verified_buyer'}, {'name':False}])
def test_invalid_findings(book, extra):
    with pytest.raises(ValueError):
        book.capture(finding(**extra))
    assert not book.state()['signals']


def test_monitor_persists_due_attempts_dedupes_and_retains_failure(book):
    calls = []
    book.fetch = lambda s: calls.append(s['id']) or 'We buy houses in Fort Wayne, Indiana in any condition.'
    assert all(s['status'] == 'checked' for s in book.refresh()['sources'])
    assert len(calls) == len(SOURCES)
    book.refresh()
    assert len(calls) == len(SOURCES)
    assert len(book.state()['signals']) == len(SOURCES)
    assert all(s['freshness'] == 'Date unknown' for s in book.state()['signals'])
    restarted = BuyerIntentBook(book.relationships.application, book.relationships)
    restarted.fetch = lambda s: pytest.fail('Restart must not refetch before due')
    restarted.refresh()
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with book.database.session(write=True) as (c, _):
        c.execute('UPDATE buyer_intent_sources SET attempted_at=?', (old,))
    book.fetch = lambda s: (_ for _ in ()).throw(ValueError('bad remote data'))
    book.fetch_posts = book.fetch
    book.refresh()
    assert all(s['error'] and s['checked_at'] for s in book.state()['sources'])
    assert all(len(s['history']) == 1 for s in book.state()['signals'])
    assert all('bad remote data' not in s['error'] for s in book.state()['sources'])


def test_monitor_paused_changed_claim_and_concurrency(book):
    book.settings({'enabled':False})
    book.fetch = lambda s: pytest.fail('Paused monitor must not fetch')
    assert book.refresh()['status'] == 'paused'
    book.settings({'enabled':True})
    book.fetch = lambda s: 'Looking for cash buyers in Fort Wayne; join our buyer list.'
    assert all(s['status'] == 'failed' for s in book.refresh()['sources'] if s['id'] in {x['id'] for x in SOURCES})
    assert not book.state()['signals']
    book.lock.acquire()
    try:
        assert book.refresh()['status'] == 'already_running'
    finally:
        book.lock.release()
    with pytest.raises(ValueError):
        book.settings({'enabled':1})


def test_prospect_is_idempotent_unqualified_and_permission_unknown(book):
    sid = book.capture(finding())['id']
    result = book.relationship(sid, {})
    assert book.relationship(sid, {}) == result
    state = book.relationships.state()
    assert len(state['relationships']) == 1
    assert not state['buyers']
    record = state['relationships'][0]
    assert record['profile']['permission'] == 'unknown'
    assert record['profile']['status'] == 'prospect'
    assert not record['qualification']
    assert record['profile']['source_reference'] == finding()['url']
    another = book.capture(finding(url='https://example.com/second'))['id']
    assert book.relationship(another, {}) == {'existing_relationships':[result['relationship_id']]}
    assert len(book.relationships.state()['relationships']) == 1
    book.review(another, {'status':'dismissed'})
    with pytest.raises(ValueError):
        book.relationship(another, {})


def test_scheduler_runs_and_stops(book):
    called = threading.Event()
    book.refresh = lambda: called.set()
    scheduler = BuyerIntentScheduler(book)
    scheduler.start()
    assert called.wait(2)
    scheduler.stop()
    assert not scheduler.thread.is_alive()


def test_http_origin_and_capture_never_fetch_submitted_url(tmp_path):
    app = Application(tmp_path / 'api.db')
    server = create_server(app.database.path, port=0, application=app)
    app.buyer_intent.fetch = lambda s: pytest.fail('Capture must not fetch submitted URL')
    origin = 'http://127.0.0.1:' + str(server.server_address[1])
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        def post(data, headers):
            return urlopen(Request(origin + '/api/buyer-intent', data=json.dumps(data).encode(),
                                   headers={'Content-Type':'application/json', **headers}, method='POST'))
        for headers in ({}, {'Origin':'https://evil.example'}):
            with pytest.raises(HTTPError) as denied:
                post(finding(), headers)
            assert denied.value.code == 403
        with post(finding(), {'Origin':origin}) as response:
            assert json.load(response)['id']
        with urlopen(origin + '/api/buyer-intent') as response:
            assert json.load(response)['summary']['total'] == 1
        with urlopen(origin + '/buyer-intent') as response:
            assert b'Who is actually buying?' in response.read()
    finally:
        server.shutdown(); server.server_close(); worker.join()


def test_negative_claims_and_distinct_authors_not_merged(book):
    assert classify("We don't buy houses anymore.", "company") == 'unclear'
    assert classify("We are no longer buying homes.", "post") == 'unclear'
    assert classify("We buy houses. What if I don’t need a fast closing? At Buy My House, we close on your schedule.", "company") == 'company_claim'
    book.capture(finding())
    with pytest.raises(ValueError, match='another author'):
        book.capture(finding(name='Someone else'))
    assert book.state()['signals'][0]['name'] == 'Synthetic Buyer'


def test_page_reader_rejects_redirects_and_large_responses_and_ignores_scripts(monkeypatch):
    import httpx
    from app import buyer_intent
    original = httpx.Client
    responses = [httpx.Response(302, headers={'location':'http://127.0.0.1/private'}),
                 httpx.Response(200, headers={'content-type':'text/html'}, text='<head><title>Marketing title</title></head><script>We buy houses</script><p>Public text only</p>'),
                 httpx.Response(200, headers={'content-type':'text/html'}, content=b'a' * 1_000_001)]
    calls = []
    def respond(request):
        calls.append(str(request.url))
        return responses.pop(0)
    monkeypatch.setattr(buyer_intent.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    with pytest.raises(ValueError):
        buyer_intent.fetch_page(SOURCES[0])
    assert buyer_intent.fetch_page(SOURCES[0]) == 'Public text only'
    with pytest.raises(ValueError, match='reading limit'):
        buyer_intent.fetch_page(SOURCES[0])
    assert calls == [SOURCES[0]['url']] * 3
