"""Button-triggered official sale notices; no offers or automatic deal creation."""
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
import threading
from urllib.request import Request, build_opener
from uuid import uuid4
from zoneinfo import ZoneInfo

from .providers import NoRedirect, parcel_key

SOURCES = {
    'accdc': {'name': 'Allen County ACCDC availability', 'url': 'https://www.allencounty.in.gov/334/ACCDC-Properties'},
    'north_campus': {'name': 'Allen County North Campus sale notice', 'url': 'https://www.allencounty.in.gov/1305/Sale-of-North-Campus-Property'},
}


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}: self.skip += 1

    def handle_endtag(self, tag):
        if tag in {'script', 'style'} and self.skip: self.skip -= 1
        if tag in {'p', 'div', 'li', 'h1', 'h2', 'br'}: self.parts.append(' ')

    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def parse_notice(source_id, body, now):
    parser = PageText(); parser.feed(body)
    text = ' '.join(' '.join(parser.parts).split())
    if source_id == 'accdc':
        if 'ACCDC Properties' not in text: raise ValueError('Source layout changed')
        phrase = re.search(r'Currently there are no properties available\.[^.]{0,150}', text, re.I)
        return {'status': 'no_inventory' if phrase else 'needs_review', 'candidates': [],
                'excerpt': phrase.group(0) if phrase else 'Availability format changed; open the original source.'}
    if 'Sale of North Campus Property' not in text: raise ValueError('Source layout changed')
    address = re.search(r'located at ([0-9]+ [A-Za-z ]+Road), Fort Wayne, Indiana', text)
    parcels = list(dict.fromkeys(re.findall(r'02-\d{2}-\d{2}-\d{3}-\d{3}\.\d{3}-\d{3}', text)))
    amount = re.search(r'No bid for less than .{0,100}?\(\$([\d,]+\.\d{2})\)', text)
    dates = re.findall(r'(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (\d{4}),? at (\d{1,2}) (AM|PM) eastern time', text)
    if not address or not parcels or not amount or len(dates) != 2:
        raise ValueError('Sale notice fields changed; no candidate saved')
    for value in parcels: parcel_key(value.replace('.', ''))
    price = float(amount.group(1).replace(',', ''))
    if not 0 < price <= 1000000000: raise ValueError('Invalid advertised minimum bid')
    window = [datetime.strptime(' '.join(value), '%B %d %Y %I %p').replace(tzinfo=ZoneInfo('America/Indiana/Indianapolis')) for value in dates]
    if window[0] >= window[1]: raise ValueError('Sale notice dates conflict')
    status = 'before_advertised_window' if now < window[0] else 'advertised_window' if now < window[1] else 'past_advertised_window'
    terms = text[text.index('No bid for less than'):text.index('No bid for less than') + 1200]
    return {'status': status, 'excerpt': terms, 'candidates': [{
        'address': address.group(1), 'city': 'Fort Wayne', 'state': 'IN', 'parcel_ids': parcels,
        'minimum_bid': price, 'bid_start': window[0].isoformat(),
        'bid_end': window[1].isoformat(), 'identity_note': 'Notice covers multiple parcels, including portions. Review survey and terms.',
        'availability': 'Advertised notice only; verify current availability with the county.',
    }]}


def fetch_notice(url):
    request = Request(url, headers={'User-Agent': 'ClubSP/0.1 bounded official sale notice check', 'Accept': 'text/html'})
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        if 'text/html' not in response.headers.get('Content-Type', ''): raise ValueError('Unexpected source format')
        raw = response.read(524289)
        if len(raw) > 524288: raise ValueError('Source exceeds notice size limit')
    return raw.decode('utf-8')


class DiscoveryMixin:
    def _initialize_discovery(self):
        self.discovery_lock = threading.Lock()
        self.discovery_fetch = fetch_notice
        with self.database.session(write=True) as (connection, _):
            connection.execute('CREATE TABLE IF NOT EXISTS discovery_checks (id TEXT PRIMARY KEY, source_id TEXT NOT NULL, fetched_at TEXT NOT NULL, body TEXT NOT NULL)')

    def check_discovery(self, data):
        if set(data) != {'source_id'} or data['source_id'] not in SOURCES:
            raise ValueError('Choose a configured official notice source')
        source_id = data['source_id']; now = datetime.now(timezone.utc)
        if not self.discovery_lock.acquire(blocking=False): raise ValueError('A notice check is already running')
        try:
            with self.database.session(write=True) as (connection, _):
                latest = connection.execute('SELECT body FROM discovery_checks WHERE source_id=? ORDER BY rowid DESC LIMIT 1', (source_id,)).fetchone()
                prior = json.loads(latest['body']) if latest else None
                if prior and prior['status'] not in {'failed', 'running'} and (now - datetime.fromisoformat(prior['fetched_at'])).total_seconds() < 86400:
                    return {**prior, 'cached': True}
                count = connection.execute('SELECT COUNT(*) FROM discovery_checks WHERE source_id=? AND substr(fetched_at,1,10)=?', (source_id, now.date().isoformat())).fetchone()[0]
                if count >= 6: raise ValueError('Daily source budget reached (six attempts)')
                record = {'id': str(uuid4()), 'source_id': source_id, **SOURCES[source_id], 'fetched_at': now.isoformat(), 'status': 'running', 'candidates': []}
                connection.execute('INSERT INTO discovery_checks VALUES(?,?,?,?)', (record['id'], source_id, record['fetched_at'], json.dumps(record)))
            try:
                body = self.discovery_fetch(SOURCES[source_id]['url'])
                parsed = parse_notice(source_id, body, now)
                digest = sha256(body.encode()).hexdigest()
                record.update(parsed, content_hash=digest, changed=bool(prior and prior.get('content_hash') != digest))
            except (OSError, ValueError, UnicodeError):
                record.update(status='failed', excerpt='Source unavailable or its format changed. Open the original and review; no candidate saved.')
            with self.database.session(write=True) as (connection, _):
                connection.execute('UPDATE discovery_checks SET body=? WHERE id=?', (json.dumps(record), record['id']))
            return {**record, 'cached': False}
        finally: self.discovery_lock.release()

    def discovery_state(self):
        now = datetime.now(timezone.utc)
        with self.database.session() as (connection, _):
            policy_row = connection.execute('SELECT body FROM opportunity_policies ORDER BY rowid DESC LIMIT 1').fetchone()
            policy = json.loads(policy_row['body']) if policy_row else None
            checks = []
            for source_id, source in SOURCES.items():
                row = connection.execute('SELECT body FROM discovery_checks WHERE source_id=? ORDER BY rowid DESC LIMIT 1', (source_id,)).fetchone()
                item = json.loads(row['body']) if row else {'source_id': source_id, **source, 'status': 'not_checked', 'candidates': []}
                if row:
                    item['stale'] = (now - datetime.fromisoformat(item['fetched_at'])).total_seconds() >= 86400
                    if item['status'] == 'running': item['status'] = 'incomplete_check'
                for candidate in item['candidates']:
                    candidate['review_gaps'] = ['Review current sale terms, parcel portions, property type, funding, title, costs and exit demand.']
                    candidate['within_recorded_price_limit'] = None if not policy else candidate['minimum_bid'] <= policy['max_seller_price']
                    if candidate['within_recorded_price_limit'] is False: candidate['review_gaps'].append('Advertised minimum bid exceeds the recorded seller-price limit.')
                    if now >= datetime.fromisoformat(candidate['bid_end']): candidate['review_gaps'].append('Advertised bid period has ended; current availability is unverified.')
                checks.append(item)
            return {'sources': checks, 'automatic_checks': False, 'creates_deals': False}
