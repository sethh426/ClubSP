"""Carry saved official notices into the existing pending intake workflow."""
from datetime import datetime
import hashlib
import json
import re
from uuid import uuid4

from core.memory.models import utc_now
from .sourcing import identity
from .validation import text_field


def notice_blockers(connection, notice, candidate, property_type=None):
    now = utc_now()
    blockers = []
    latest = connection.execute('SELECT id FROM discovery_checks WHERE source_id=? ORDER BY rowid DESC LIMIT 1', (notice['source_id'],)).fetchone()
    if not latest or latest['id'] != notice['id']:
        blockers.append('Check the latest official notice before intake')
    age = (now - datetime.fromisoformat(notice['fetched_at'])).total_seconds()
    if not 0 <= age < 86400:
        blockers.append('Official notice is stale; check the source again')
    if not datetime.fromisoformat(candidate['bid_start']) <= now < datetime.fromisoformat(candidate['bid_end']):
        blockers.append('Advertised bid window is not currently open')
    row = connection.execute('SELECT body FROM opportunity_policies ORDER BY rowid DESC LIMIT 1').fetchone()
    policy = json.loads(row['body']) if row else None
    if not policy:
        blockers.append('Save a buy box and risk policy before intake')
    else:
        market = ' '.join(f"{candidate['city']}, {candidate['state']}".lower().split())
        if market not in policy.get('markets', []):
            blockers.append('Notice market is outside the recorded buy box')
        if candidate['minimum_bid'] > policy['max_seller_price']:
            blockers.append('Advertised minimum bid exceeds the recorded seller-price limit')
        if property_type and '_'.join(property_type.lower().split()) not in policy.get('property_types', []):
            blockers.append('Reviewed property type is outside the recorded buy box')
    return blockers


class DiscoveryIntakeMixin:
    def stage_discovery_intake(self, data):
        fields = {'check_id', 'candidate_index', 'parcel_id', 'zip', 'property_type', 'reviewer', 'note', 'identity_confirmed'}
        if set(data) != fields:
            raise ValueError('Use the saved notice and complete the intake review fields')
        if data['identity_confirmed'] is not True:
            raise ValueError('Confirm parcel, address, market and surveyed portions before staging')
        index = data['candidate_index']
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError('Choose a saved notice candidate')
        check_id = text_field(data, 'check_id', 100)
        reviewed = {key: text_field(data, key, limit) for key, limit in (
            ('parcel_id', 100), ('zip', 10), ('property_type', 60), ('reviewer', 120), ('note', 1000))}
        if not re.fullmatch(r'\d{5}(?:-\d{4})?', reviewed['zip']):
            raise ValueError('ZIP must be five digits or ZIP+4')
        with self.database.session(write=True) as (connection, _):
            saved = connection.execute('SELECT body FROM discovery_checks WHERE id=?', (check_id,)).fetchone()
            if not saved:
                raise LookupError('Saved notice not found')
            notice = json.loads(saved['body'])
            if notice['status'] not in {'advertised_window', 'before_advertised_window', 'past_advertised_window'} or index >= len(notice['candidates']):
                raise ValueError('This check has no usable saved candidate')
            candidate = notice['candidates'][index]
            if reviewed['parcel_id'] not in candidate['parcel_ids']:
                raise ValueError('Select a parcel explicitly named in this notice')
            blockers = notice_blockers(connection, notice, candidate, reviewed['property_type'])
            if blockers:
                raise ValueError('; '.join(blockers))
            key = 'discovery:' + hashlib.sha256(json.dumps([notice['source_id'], candidate], sort_keys=True).encode()).hexdigest()
            prior = connection.execute('SELECT id FROM sourcing_batches WHERE batch_key=?', (key,)).fetchone()
            if prior:
                old_batch = json.loads(connection.execute('SELECT body FROM sourcing_batches WHERE id=?', (prior['id'],)).fetchone()['body'])
                row = connection.execute('SELECT id,body,status FROM sourcing_rows WHERE batch_id=?', (prior['id'],)).fetchone()
                if row['status'] == 'pending':
                    old_value = json.loads(row['body'])['value']
                    if any(old_value[key] != reviewed[key] for key in ('parcel_id', 'zip', 'property_type')):
                        raise ValueError('This notice already has a different pending identity review; review the existing intake instead')
                    old_batch.update(source_date=notice['fetched_at'][:10], raw_hash=notice['content_hash'], retrieved_at=utc_now().isoformat())
                    old_batch['discovery'].update(check_id=check_id, candidate_index=index, notice=notice, reviewer=reviewed['reviewer'], note=reviewed['note'])
                    connection.execute('UPDATE sourcing_batches SET body=? WHERE id=?', (json.dumps(old_batch), prior['id']))
                return {'id': prior['id'], 'row_id': row['id'], 'duplicate': True, 'status': row['status']}

            collision = any(identity(row['address']) == identity(candidate['address']) and identity(row['city']) == identity(candidate['city']) and identity(row['state']) == identity(candidate['state']) for row in connection.execute('SELECT address,city,state FROM properties'))
            if collision:
                raise ValueError('An existing property has this address; review it instead of creating another intake')
            batch_id, row_id = str(uuid4()), str(uuid4())
            now = utc_now().isoformat()
            batch = {'id': batch_id, 'kind': 'candidates', 'provider': notice['name'], 'source_url': notice['url'],
                     'source_date': notice['fetched_at'][:10], 'rights_basis': 'Official public notice reviewed for internal property research',
                     'city': candidate['city'], 'state': candidate['state'], 'raw_hash': notice['content_hash'],
                     'retrieved_at': now, 'row_count': 1,
                     'discovery': {'check_id': check_id, 'candidate_index': index, 'notice': notice,
                                   'candidate': candidate, 'reviewer': reviewed['reviewer'], 'note': reviewed['note']}}
            value = {key: candidate[key] for key in ('address', 'city', 'state')}
            value.update({key: reviewed[key] for key in ('parcel_id', 'zip', 'property_type')})
            body = {'id': row_id, 'batch_id': batch_id, 'line': 2, 'value': value, 'raw': value, 'errors': []}
            connection.execute('INSERT INTO sourcing_batches(id,batch_key,body) VALUES(?,?,?)', (batch_id, key, json.dumps(batch)))
            connection.execute("INSERT INTO sourcing_rows(id,batch_id,body,status) VALUES(?,?,?,'pending')", (row_id, batch_id, json.dumps(body)))
            return {'id': batch_id, 'row_id': row_id, 'duplicate': False, 'status': 'pending'}

    def validate_discovery_acceptance(self, connection, batch, value):
        provenance = batch.get('discovery')
        if not provenance:
            return
        notice, candidate = provenance['notice'], provenance['candidate']
        blockers = notice_blockers(connection, notice, candidate, value['property_type'])
        if blockers:
            raise ValueError('; '.join(blockers))
        if value['parcel_id'] not in candidate['parcel_ids']:
            raise ValueError('Reviewed parcel no longer matches the saved notice')
