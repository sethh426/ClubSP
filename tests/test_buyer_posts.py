from datetime import datetime, timezone
import pytest

from app.buyer_posts import FEEDS, parse_posts, publication_day
from tests.test_buyer_intent import book


def rss(items):
    return ('<rss><channel>' + items + '</channel></rss>').encode()


def item(url='https://buysasis.com/blog/buying/', content='We buy houses in Fort Wayne, Indiana.', day='Wed, 30 Sep 2026 13:00:00 +0000'):
    return f'<item><title>Local buying post</title><link>{url}</link><pubDate>{day}</pubDate><description><![CDATA[<p>{content}</p>]]></description></item>'


def test_feed_validation_dates_links_and_limits():
    posts = parse_posts(rss(item() + item() + item('https://evil.example/post') + item('http://127.0.0.1/')), FEEDS[0])
    assert len(posts) == 1
    assert posts[0]['published_on'] == '2026-09-30'
    assert publication_day('not a date') == ''
    assert publication_day('2999-01-01T00:00:00Z') == ''
    assert publication_day('2026-09-30T10:00:00Z') == '2026-09-30'
    assert len(parse_posts(rss(''.join(item(f'https://buysasis.com/blog/{i}') for i in range(60))), FEEDS[0])) == 50
    for body in (b'<html/>', b'<rss>', b'<!DOCTYPE x [<!ENTITY x "unsafe">]><rss/>',
                 '<!DOCTYPE x [<!ENTITY x "unsafe">]><rss/>'.encode('utf-16'), b'a' * 1_000_001):
        with pytest.raises(ValueError):
            parse_posts(body, FEEDS[0])


def test_atom_uses_published_date_not_update_date():
    body = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Buying</title><link href="https://buysasis.com/blog/a"/><updated>2026-09-30T00:00:00Z</updated><summary>We buy houses in Fort Wayne.</summary></entry></feed>'
    assert parse_posts(body, FEEDS[0])[0]['published_on'] == ''


def test_discovery_retains_provenance_filters_education_and_deduplicates(book):
    posts = parse_posts(rss(item() + item('https://buysasis.com/blog/education', 'Here are tips for home buyers in Fort Wayne.')
                            + item('https://buysasis.com/blog/other', 'We buy houses in Florida.')
                            + item('https://buysasis.com/blog/network', 'Looking for cash buyers in Fort Wayne.')), FEEDS[0])
    book.fetch_posts = lambda source: posts
    book.fetch = lambda source: 'We buy houses in Fort Wayne.'
    first = book.refresh()
    feed_result = next(r for r in first['sources'] if r['id'] == FEEDS[0]['id'])
    assert (feed_result['scanned'], feed_result['imported'], feed_result['skipped']) == (4, 1, 3)
    state = book.state()
    signal = next(s for s in state['signals'] if s.get('discovered_via'))
    assert signal['category'] == 'company_claim'  # Never promote marketing into a buyer request.
    assert signal['title'] == 'Local buying post'
    assert signal['published_on'] == '2026-09-30'
    assert signal['discovered_via'] == FEEDS[0]['url']
    assert state['summary']['buying_requests'] == 0
    assert state['post_runs'][0]['status'] == 'completed'
    book.review(signal['id'], {'status':'dismissed'})
    assert all(s['status'] == 'not_due' for s in book.refresh()['sources'])
    book.collect_posts(FEEDS[0], datetime.now(timezone.utc))
    signal = next(s for s in book.state()['signals'] if s.get('discovered_via'))
    assert signal['status'] == 'dismissed'
    assert len(signal['history']) == 1
    assert not book.relationships.state()['buyers']


def test_feed_failure_visible_and_restart_and_pause_prevent_calls(book):
    book.fetch = lambda s: 'We buy houses in Fort Wayne.'
    book.fetch_posts = lambda s: (_ for _ in ()).throw(ValueError('bad feed'))
    book.refresh()
    assert book.state()['post_runs'][0]['status'] == 'failed'
    source = next(s for s in book.state()['sources'] if s['id'] == FEEDS[0]['id'])
    assert source['checked_at'] is None and source['error']
    from app.buyer_intent import BuyerIntentBook
    restarted = BuyerIntentBook(book.relationships.application, book.relationships)
    restarted.fetch_posts = lambda s: pytest.fail('Persisted attempt must prevent new I/O')
    restarted.refresh()
    restarted.settings({'enabled': False})
    assert restarted.refresh()['status'] == 'paused'


def test_one_conflicting_author_does_not_discard_other_posts(book):
    from tests.test_buyer_intent import finding
    book.capture(finding(url='https://buysasis.com/blog/conflict', name='Original author'))
    book.fetch_posts = lambda s: parse_posts(rss(item('https://buysasis.com/blog/conflict') + item()), FEEDS[0])
    stats = book.collect_posts(FEEDS[0], datetime.now(timezone.utc))
    assert stats == {'scanned':2, 'imported':1, 'skipped':1}
    assert next(s for s in book.state()['signals'] if s['url'].endswith('conflict'))['name'] == 'Original author'


def test_feed_reader_uses_bounded_public_transport(monkeypatch):
    from app import buyer_posts
    from types import SimpleNamespace
    calls = []
    def fetch(url, **kwargs):
        calls.append((url,kwargs))
        return SimpleNamespace(content_type='application/rss+xml', body=rss(item()))
    monkeypatch.setattr(buyer_posts, 'fetch_source', fetch)
    assert len(buyer_posts.fetch_posts(FEEDS[0])) == 1
    assert calls[0][1]['max_bytes'] == 1_000_000
    monkeypatch.setattr(buyer_posts, 'fetch_source', lambda *args,**kwargs: SimpleNamespace(content_type='text/html'))
    with pytest.raises(ValueError, match='did not return a feed'):
        buyer_posts.fetch_posts(FEEDS[0])
