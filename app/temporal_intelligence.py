"""Observed source changes and durable, bounded adaptive acquisition timing."""
from __future__ import annotations

import json
import time
from uuid import uuid4

from .evidence_compiler import canonical, digest, integer, label, records
from .schema import assert_component_compatible, ensure_component


def signatures(payload, identity_field=None):
    grouped = {}
    for row in records(payload):
        identity = row.get(identity_field) if identity_field else None
        key = str(identity).strip() if identity is not None else digest(row)
        fields = {name: digest(value) for name, value in row.items()}
        grouped.setdefault(key, []).append(fields)
    return {key: sorted(values, key=canonical) for key, values in sorted(grouped.items())}


def change_details(before, after):
    before_keys, after_keys = set(before), set(after)
    common = before_keys & after_keys
    changed = sorted(key for key in common if before[key] != after[key])
    fields = {}
    for key in changed[:100]:
        old_fields, new_fields = {}, {}
        for row in before[key]:
            for name, value in row.items(): old_fields.setdefault(name, set()).add(value)
        for row in after[key]:
            for name, value in row.items(): new_fields.setdefault(name, set()).add(value)
        fields[key] = sorted(f for f in set(old_fields) | set(new_fields) if old_fields.get(f) != new_fields.get(f))
    return {'changed_records':changed[:100], 'changed_fields':fields,
            'new_in_sample':sorted(after_keys - before_keys)[:100],
            'absent_from_sample':sorted(before_keys - after_keys)[:100],
            'total_changed_records':len(changed), 'sample_complete':False}


class TemporalIntelligenceMixin:
    def _initialize_temporal(self):
        with self.database.session(write=True) as (c, _):
            assert_component_compatible(c, 'temporal_intelligence')
            c.execute('''CREATE TABLE IF NOT EXISTS temporal_policies (
                sentra_id TEXT PRIMARY KEY, activation TEXT NOT NULL, body TEXT NOT NULL,
                version TEXT NOT NULL, reviewed_at REAL NOT NULL
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS temporal_policy_versions (
                version TEXT PRIMARY KEY, sentra_id TEXT NOT NULL, body TEXT NOT NULL, reviewed_at REAL NOT NULL
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS temporal_sources (
                series_id TEXT PRIMARY KEY, sentra_id TEXT NOT NULL, activation TEXT NOT NULL,
                signature TEXT NOT NULL, records_json TEXT NOT NULL, observed_at REAL NOT NULL,
                interval_seconds INTEGER NOT NULL, next_due REAL NOT NULL, lease_until REAL NOT NULL,
                lease_token TEXT NOT NULL, observations INTEGER NOT NULL, changes INTEGER NOT NULL,
                stable_streak INTEGER NOT NULL, last_change_at REAL, ewma_change_seconds REAL,
                last_error TEXT NOT NULL, UNIQUE(sentra_id,activation)
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS temporal_events (
                id TEXT PRIMARY KEY, series_id TEXT NOT NULL REFERENCES temporal_sources(series_id),
                kind TEXT NOT NULL, signature TEXT NOT NULL, previous_signature TEXT NOT NULL,
                details_json TEXT NOT NULL, observed_at REAL NOT NULL
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS temporal_event_series ON temporal_events(series_id,observed_at)')
            c.execute('''CREATE TABLE IF NOT EXISTS temporal_attempts (
                id TEXT PRIMARY KEY, series_id TEXT NOT NULL REFERENCES temporal_sources(series_id),
                status TEXT NOT NULL, cost_cents INTEGER NOT NULL, created_at REAL NOT NULL, error TEXT NOT NULL
            )''')
            ensure_component(c, 'temporal_intelligence')

    @staticmethod
    def _series_id(row):
        return digest({'source':row['id'], 'activation':row['activated_at']})

    def temporal_policy(self, data):
        if data.get('owner_reviewed') is not True:
            raise ValueError('temporal acquisition policy requires owner_reviewed=true')
        row = self._active_source(data.get('sentra_id'))
        maximum = min(row['freshness_target_hours'] * 3600, 7 * 86400)
        minimum = integer(data.get('min_interval_seconds', 3600), 'min_interval_seconds', 3600, maximum)
        base = integer(data.get('base_interval_seconds', min(21600, maximum)), 'base_interval_seconds', minimum, maximum)
        enabled = data.get('enabled', True)
        if type(enabled) is not bool: raise ValueError('enabled must be a boolean')
        policy = {'sentra_id':row['id'], 'enabled':enabled, 'min_interval_seconds':minimum,
                  'base_interval_seconds':base, 'max_interval_seconds':maximum,
                  'daily_call_limit':integer(data.get('daily_call_limit', 4), 'daily_call_limit', 1, 24),
                  'daily_cost_limit_cents':integer(data.get('daily_cost_limit_cents', 0), 'daily_cost_limit_cents', 0, 100000),
                  'cost_cents':integer(data.get('cost_cents', 0), 'cost_cents', 0, 100000),
                  'note':label(data.get('note'), 'note', 4000)}
        now, version, series_id = time.time(), digest({'activation':row['activated_at'],'policy':policy}), self._series_id(row)
        with self.database.session(write=True) as (c, _):
            active = c.execute('SELECT activated_at FROM activated_sentras WHERE id=?', (row['id'],)).fetchone()
            if not active or active[0] != row['activated_at']: raise ValueError('source activation changed')
            c.execute('INSERT INTO temporal_policies VALUES(?,?,?,?,?) ON CONFLICT(sentra_id) DO UPDATE SET activation=excluded.activation,body=excluded.body,version=excluded.version,reviewed_at=excluded.reviewed_at',
                      (row['id'],row['activated_at'],canonical(policy),version,now))
            c.execute('INSERT OR IGNORE INTO temporal_policy_versions VALUES(?,?,?,?)', (version,row['id'],canonical(policy),now))
            c.execute("INSERT OR IGNORE INTO temporal_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (series_id,row['id'],row['activated_at'],'','{}',0,base,now,0,'',0,0,0,None,None,''))
            c.execute('UPDATE temporal_sources SET interval_seconds=?,next_due=? WHERE series_id=?', (base,now,series_id))
        return {**policy,'version':version,'activation':row['activated_at']}

    def temporal_observe_result(self, row, result, observed_at=None):
        now = time.time() if observed_at is None else observed_at
        series_id = self._series_id(row)
        with self.database.session(write=True) as (c, _):
            current = c.execute('SELECT activated_at FROM activated_sentras WHERE id=?', (row['id'],)).fetchone()
            if not current or current[0] != row['activated_at']:
                raise ValueError('source activation changed before temporal recording')
            mapping = c.execute('SELECT body,version FROM evidence_profiles WHERE sentra_id=? AND activation=?', (row['id'],row['activated_at'])).fetchone()
            identity = json.loads(mapping['body'])['identity_field'] if mapping else None
            # Mapping version is part of the signature domain; changed mappings
            # start a new comparison baseline rather than invent a source change.
            domain = mapping['version'] if mapping else 'unmapped'
            signature_rows = signatures(result['payload'], identity)
            signature = digest({'domain':domain,'records':signature_rows})
            policy_row = c.execute('SELECT body FROM temporal_policies WHERE sentra_id=? AND activation=?', (row['id'],row['activated_at'])).fetchone()
            policy = json.loads(policy_row[0]) if policy_row else {'min_interval_seconds':3600,'max_interval_seconds':min(row['freshness_target_hours']*3600,7*86400),'base_interval_seconds':min(21600,row['freshness_target_hours']*3600)}
            base = policy['base_interval_seconds']
            c.execute('INSERT OR IGNORE INTO temporal_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (series_id,row['id'],row['activated_at'],'','{}',0,base,now,0,'',0,0,0,None,None,''))
            previous = c.execute('SELECT * FROM temporal_sources WHERE series_id=?', (series_id,)).fetchone()
            if now <= previous['observed_at']:
                return {'recorded':False,'reason':'out-of-order observation'}
            before = json.loads(previous['records_json'])
            old_domain = before.get('domain')
            baseline = not previous['signature'] or old_domain != domain
            changed = not baseline and previous['signature'] != signature
            interval, streak = previous['interval_seconds'], previous['stable_streak']
            last_change, ewma = previous['last_change_at'], previous['ewma_change_seconds']
            if baseline:
                kind, details, streak, interval = 'baseline', {'record_count':sum(len(v) for v in signature_rows.values())}, 0, base
            elif changed:
                kind, details, streak = 'changed', change_details(before.get('records',{}),signature_rows), 0
                interval = max(policy['min_interval_seconds'], interval // 2)
                if last_change is not None and now > last_change:
                    gap = now - last_change
                    ewma = gap if ewma is None else .3 * gap + .7 * ewma
                    interval = min(interval, max(policy['min_interval_seconds'], int(ewma / 2)))
                last_change = now
            else:
                spaced = now - previous['observed_at'] >= policy['min_interval_seconds']
                kind, details, streak = 'stable', {'record_count':sum(len(v) for v in signature_rows.values())}, streak + int(spaced)
                if streak >= 3:
                    interval = int(interval * 1.5)
                    streak = 0
            interval = max(policy['min_interval_seconds'], min(interval,policy['max_interval_seconds']))
            details.update({'raw_payload_hash':result['metadata']['raw_payload_hash'], 'payload_hash':result['payload_sha256'],
                            'source_url':result['source_url'],'duration_ms':result['duration_ms'],
                            'interval_seconds':interval,'mapping_version':domain})
            c.execute('UPDATE temporal_sources SET signature=?,records_json=?,observed_at=?,interval_seconds=?,next_due=?,observations=observations+1,changes=changes+?,stable_streak=?,last_change_at=?,ewma_change_seconds=?,last_error=? WHERE series_id=?',
                      (signature,canonical({'domain':domain,'records':signature_rows}),now,interval,now+interval,int(changed),streak,last_change,ewma,'',series_id))
            event_id = str(uuid4())
            c.execute('INSERT INTO temporal_events VALUES(?,?,?,?,?,?,?)', (event_id,series_id,kind,signature,previous['signature'],canonical(details),now))
        return {'recorded':True,'event_id':event_id,'kind':kind,'interval_seconds':interval}

    def temporal_cycle(self, data=None, now=None):
        data = data or {}
        limit = integer(data.get('max_sources',3), 'max_sources', 1, 10)
        now = time.time() if now is None else now
        claimed, blocked = [], []
        with self.database.session(write=True) as (c, _):
            rows = c.execute('''SELECT s.*,p.body FROM temporal_sources s
                JOIN temporal_policies p ON p.sentra_id=s.sentra_id AND p.activation=s.activation
                JOIN activated_sentras a ON a.id=s.sentra_id AND a.activated_at=s.activation
                JOIN meta_source_candidates m ON m.fingerprint=a.candidate_fingerprint AND m.state='active'
                WHERE s.next_due<=? AND s.lease_until<=? AND json_extract(p.body,'$.enabled')=1
                ORDER BY s.next_due,s.sentra_id LIMIT 100''', (now,now)).fetchall()
            for row in rows:
                if len(claimed) >= limit: break
                policy = json.loads(row['body'])
                if not policy['enabled']: continue
                count, cost = c.execute('SELECT count(*),coalesce(sum(cost_cents),0) FROM temporal_attempts WHERE series_id=? AND created_at>?', (row['series_id'],now-86400)).fetchone()
                if count >= policy['daily_call_limit'] or cost + policy['cost_cents'] > policy['daily_cost_limit_cents']:
                    blocked.append({'sentra_id':row['sentra_id'],'status':'budget_limited'})
                    oldest = c.execute('SELECT min(created_at) FROM temporal_attempts WHERE series_id=? AND created_at>?', (row['series_id'],now-86400)).fetchone()[0]
                    retry_at = oldest + 86400 + 1 if oldest is not None else now + 3600
                    c.execute("UPDATE temporal_sources SET last_error='rolling daily acquisition budget exhausted',next_due=? WHERE series_id=?", (retry_at,row['series_id']))
                    continue
                token = str(uuid4())
                c.execute('UPDATE temporal_sources SET lease_until=?,lease_token=? WHERE series_id=?', (now+600,token,row['series_id']))
                c.execute('INSERT INTO temporal_attempts VALUES(?,?,?,?,?,?)', (token,row['series_id'],'reserved',policy['cost_cents'],now,''))
                claimed.append((dict(row),token))
        results = []
        for row, token in claimed:
            error = ''
            try:
                result = self.meta_execute({'sentra_id':row['sentra_id']})
                results.append({'sentra_id':row['sentra_id'],'status':'observed','payload_hash':result['payload_sha256']})
            except Exception as exc:
                error = type(exc).__name__ + '; inspect source history'
                results.append({'sentra_id':row['sentra_id'],'status':'failed','error':error})
            finally:
                with self.database.session(write=True) as (c, _):
                    c.execute('UPDATE temporal_attempts SET status=?,error=? WHERE id=?', ('failed' if error else 'success',error,token))
                    c.execute("UPDATE temporal_sources SET lease_until=0,lease_token='',last_error=?,next_due=case when ?!='' then ? else next_due end WHERE series_id=? AND lease_token=?",
                              (error,error,now+3600,row['series_id'],token))
        return {'checked':len(results),'results':results,'blocked':blocked,'automatic_activation':False}

    def temporal_state(self):
        with self.database.session() as (c, _):
            sources = [dict(r) for r in c.execute('SELECT * FROM temporal_sources ORDER BY observed_at DESC LIMIT 100')]
            policies = [{**json.loads(r['body']),'activation':r['activation'],'version':r['version']} for r in c.execute('SELECT * FROM temporal_policies ORDER BY sentra_id')]
            events = [{**dict(r),'details':json.loads(r['details_json'])} for r in c.execute('SELECT * FROM temporal_events ORDER BY observed_at DESC,rowid DESC LIMIT 100')]
        for source in sources:
            source.pop('records_json'); source.pop('lease_token')
        for event in events: event.pop('details_json')
        return {'sources':sources,'policies':policies,'events':events,'observations_are_bounded_samples':True}
