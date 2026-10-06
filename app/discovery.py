"""Button-triggered official sale notices; no offers or automatic deal creation."""
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import io
import calendar
import json
import re
import threading
from urllib.error import HTTPError
from urllib.request import Request, build_opener
from urllib.parse import urljoin
from uuid import uuid4
from zoneinfo import ZoneInfo
from pypdf import PdfReader

from .providers import NoRedirect, parcel_key
from .discovery_intake import DiscoveryIntakeMixin, notice_blockers, preliminary_notice_buyers
from .schema import assert_component_compatible, ensure_component

SOURCES = {
    'accdc': {'name': 'Allen County ACCDC availability', 'url': 'https://www.allencounty.in.gov/334/ACCDC-Properties'},
    'north_campus': {'name': 'Allen County North Campus sale notice', 'url': 'https://www.allencounty.in.gov/1305/Sale-of-North-Campus-Property'},
    'sheriff_sales': {'name': 'Allen County Sheriff mortgage foreclosure sales', 'url': 'https://www.allencountysheriff.org/2026-sheriff-sales/'},
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


class AnchorLinks(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []; self.href = None; self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.href = dict(attrs).get('href')
            self.parts = []

    def handle_data(self, data):
        if self.href is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.href is not None:
            self.links.append((self.href, ' '.join(' '.join(self.parts).split())))
            self.href = None
            self.parts = []


def _bounded_response(url, accept, limit):
    request = Request(url, headers={'User-Agent': 'ClubSP/0.1 bounded official sale notice check', 'Accept': accept})
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        raw = response.read(limit + 1)
        content_type = response.headers.get('Content-Type', '')
        final_url = response.geturl()
    if len(raw) > limit:
        raise ValueError('Source exceeds notice size limit')
    return raw, content_type, final_url


def fetch_sheriff_sales(index_url, now):
    raw, content_type, _ = _bounded_response(index_url, 'text/html', 524288)
    if 'text/html' not in content_type:
        raise ValueError('Unexpected sheriff source format')
    html = raw.decode('utf-8')
    parser = AnchorLinks(); parser.feed(html)
    local = now.astimezone(ZoneInfo('America/Indiana/Indianapolis'))
    wanted = []
    year, month = local.year, local.month
    for offset in range(3):
        m = month + offset
        y = year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        wanted.append(f'{calendar.month_name[m].upper()} {y}')
    links = {}
    for href, label in parser.links:
        key = label.strip().upper()
        if key in wanted and href.lower().endswith('.pdf'):
            links[key] = urljoin(index_url, href)
    if not links:
        raise ValueError('No current sheriff-sale documents were found on the official index')
    documents = []
    for label in wanted:
        url = links.get(label)
        if not url:
            continue
        pdf, pdf_type, final_url = _bounded_response(url, 'application/pdf', 1048576)
        if 'application/pdf' not in pdf_type:
            raise ValueError('Unexpected sheriff-sale document format')
        reader = PdfReader(io.BytesIO(pdf), strict=False)
        if not 1 <= len(reader.pages) <= 10:
            raise ValueError('Sheriff-sale document has an unsupported page count')
        text = '\n'.join(page.extract_text(extraction_mode='layout') or '' for page in reader.pages)
        if len(text) > 200000:
            raise ValueError('Sheriff-sale extracted text exceeds supported size')
        documents.append(f'[[SOURCE_DOCUMENT:{final_url}]]\n{text}')
    if not documents:
        raise ValueError('Current sheriff-sale documents could not be read')
    return '\n'.join(documents)


_SHERIFF_ROW = re.compile(
    r'^\s*\d+\s+(?P<sale>\d{1,2}/\d{1,2}/\d{4})\s+'
    r'(?P<cause>\S+-MF-\S+)\s+'
    r'(?P<address>.+? FORT WAYNE[,.] IN \d{5})\s+'
    r'(?:(?P<cancel>\d{1,2}/\d{1,2}/\d{4})\s+)?'
    r'\$\s*(?P<judgment>[\d,]+\.\d{2})\b'
)


def parse_sheriff_sales(body, now):
    local_today = now.astimezone(ZoneInfo('America/Indiana/Indianapolis')).date()
    candidates, current_document = [], ''
    saw_no_sales, saw_rows, cancelled, past = False, 0, 0, 0
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith('[[SOURCE_DOCUMENT:') and stripped.endswith(']]'):
            current_document = stripped[len('[[SOURCE_DOCUMENT:'):-2]
            continue
        if 'NO SALES FOR THE MONTH' in stripped.upper():
            saw_no_sales = True
        match = _SHERIFF_ROW.match(line)
        if not match:
            continue
        saw_rows += 1
        sale_date = datetime.strptime(match.group('sale'), '%m/%d/%Y').date()
        cancel_date = match.group('cancel') or ''
        if cancel_date:
            cancelled += 1
            continue
        if sale_date < local_today:
            past += 1
            continue
        full_address = ' '.join(match.group('address').split()).replace(' FORT WAYNE. IN ', ' FORT WAYNE, IN ')
        before_city, zip_code = full_address.rsplit(' FORT WAYNE, IN ', 1)
        amount = float(match.group('judgment').replace(',', ''))
        if not 0 < amount <= 1000000000:
            raise ValueError('Invalid sheriff-sale judgment amount')
        candidates.append({
            'address': before_city, 'city': 'Fort Wayne', 'state': 'IN', 'zip': zip_code,
            'cause_number': match.group('cause'), 'sale_date': sale_date.isoformat(),
            'judgment_amount': amount, 'cancellation_date': '',
            'parcel_ids': [], 'minimum_bid': None, 'intake_supported': False,
            'price_basis': 'Judgment amount is recorded notice data, not a purchase price or guaranteed opening bid.',
            'identity_note': 'Sheriff notice does not provide parcel identity. Confirm the parcel independently before intake.',
            'availability': 'Scheduled sheriff sale notice; verify current status because sales may be cancelled or changed.',
            'source_document_url': current_document,
        })
    candidates.sort(key=lambda item: (item['sale_date'], item['address'], item['cause_number']))
    if candidates:
        status = 'scheduled_sales'
    elif saw_rows and cancelled:
        status = 'no_active_sales'
    elif saw_rows and past:
        status = 'past_sales'
    elif saw_no_sales:
        status = 'no_inventory'
    else:
        raise ValueError('Sheriff-sale document layout changed')
    excerpt = (
        f'{len(candidates)} current Fort Wayne sheriff-sale candidate(s); '
        f'{cancelled} cancelled row(s) excluded and {past} past row(s) excluded. '
        'Judgment amounts are not treated as acquisition prices.'
    )
    return {'status': status, 'candidates': candidates, 'excerpt': excerpt}


def parse_notice(source_id, body, now):
    if source_id == 'sheriff_sales':
        return parse_sheriff_sales(body, now)
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


class DiscoveryMixin(DiscoveryIntakeMixin):
    def _initialize_discovery(self):
        self.discovery_lock = threading.Lock()
        self.discovery_fetch = fetch_notice
        self.sheriff_discovery_fetch = fetch_sheriff_sales
        with self.database.session(write=True) as (connection, _):
            assert_component_compatible(connection, "discovery")
            connection.execute('CREATE TABLE IF NOT EXISTS discovery_checks (id TEXT PRIMARY KEY, source_id TEXT NOT NULL, fetched_at TEXT NOT NULL, body TEXT NOT NULL)')
            ensure_component(connection, "discovery")

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
                body = (self.sheriff_discovery_fetch(SOURCES[source_id]['url'], now)
                        if source_id == 'sheriff_sales' else self.discovery_fetch(SOURCES[source_id]['url']))
                parsed = parse_notice(source_id, body, now)
                digest = sha256(body.encode()).hexdigest()
                record.update(parsed, content_hash=digest, changed=bool(prior and prior.get('content_hash') != digest))
            except HTTPError as error:
                if error.code in {401, 403, 429}:
                    record.update(
                        status='source_access_blocked',
                        excerpt='The official source refused this server-side request. Open the configured official page and record a reviewed source snapshot; no candidate was created from the blocked request.',
                        retrieval_mode='server_fetch',
                    )
                else:
                    record.update(status='failed', excerpt='Official source request failed. Open the original and review; no candidate saved.', retrieval_mode='server_fetch')
            except (OSError, ValueError, UnicodeError):
                record.update(status='failed', excerpt='Source unavailable or its format changed. Open the original and review; no candidate saved.', retrieval_mode='server_fetch')
            with self.database.session(write=True) as (connection, _):
                connection.execute('UPDATE discovery_checks SET body=? WHERE id=?', (json.dumps(record), record['id']))
            return {**record, 'cached': False}
        finally: self.discovery_lock.release()

    def record_discovery_snapshot(self, data):
        if not isinstance(data, dict) or set(data) != {'source_id', 'source_url', 'body', 'reviewer', 'note'}:
            raise ValueError('Record the configured source, exact URL, bounded source text, reviewer and note')
        source_id = data.get('source_id')
        if source_id not in SOURCES:
            raise ValueError('Choose a configured official notice source')
        source = SOURCES[source_id]
        if data.get('source_url') != source['url']:
            raise ValueError('Source URL must exactly match the configured official source')
        body = data.get('body')
        reviewer = data.get('reviewer')
        note = data.get('note')
        if not isinstance(body, str) or not body.strip() or len(body) > 100000:
            raise ValueError('Official source snapshot must contain 1 to 100000 characters')
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer.strip()) > 120:
            raise ValueError('Reviewer is required')
        if not isinstance(note, str) or not note.strip() or len(note.strip()) > 1000:
            raise ValueError('Review note is required')
        now = datetime.now(timezone.utc)
        parsed = parse_notice(source_id, body, now)
        digest = sha256(body.encode()).hexdigest()
        record = {
            'id': str(uuid4()), 'source_id': source_id, **source,
            'fetched_at': now.isoformat(), **parsed, 'content_hash': digest,
            'changed': False, 'retrieval_mode': 'reviewed_official_snapshot',
            'reviewer': reviewer.strip(), 'review_note': note.strip(),
        }
        with self.database.session(write=True) as (connection, _):
            latest = connection.execute(
                'SELECT body FROM discovery_checks WHERE source_id=? ORDER BY rowid DESC LIMIT 1',
                (source_id,),
            ).fetchone()
            if latest:
                prior = json.loads(latest['body'])
                record['changed'] = bool(prior.get('content_hash') and prior.get('content_hash') != digest)
            connection.execute(
                'INSERT INTO discovery_checks VALUES(?,?,?,?)',
                (record['id'], source_id, record['fetched_at'], json.dumps(record)),
            )
        return record

    def discovery_state(self):
        now = datetime.now(timezone.utc)
        with self.database.session() as (connection, _):
            checks = []
            for source_id, source in SOURCES.items():
                row = connection.execute('SELECT body FROM discovery_checks WHERE source_id=? ORDER BY rowid DESC LIMIT 1', (source_id,)).fetchone()
                item = json.loads(row['body']) if row else {'source_id': source_id, **source, 'status': 'not_checked', 'candidates': []}
                if row:
                    item['stale'] = (now - datetime.fromisoformat(item['fetched_at'])).total_seconds() >= 86400
                    if item['status'] == 'running': item['status'] = 'incomplete_check'
                for candidate in item['candidates']:
                    candidate['intake_blockers'] = notice_blockers(connection, item, candidate)
                    if item['source_id'] == 'sheriff_sales':
                        candidate['review_gaps'] = [
                            'Confirm the exact parcel identity from Allen County records before intake.',
                            'Verify the sheriff sale is still scheduled and has not been cancelled or changed.',
                            'Judgment amount is not treated as an acquisition price or guaranteed opening bid.',
                            'Review title, liens, occupancy, condition, repairs, funding, fees and exit demand before any deal analysis.',
                        ]
                    else:
                        candidate['review_gaps'] = ['Review current sale terms, parcel portions, property type, funding, title, costs and exit demand.']
                        if candidate.get('bid_end') and now >= datetime.fromisoformat(candidate['bid_end']):
                            candidate['review_gaps'].append('Advertised bid period has ended; current availability is unverified.')
                    candidate['buyer_criteria'] = preliminary_notice_buyers(connection, candidate)
                checks.append(item)
            return {'sources': checks, 'automatic_checks': False, 'creates_deals': False}
