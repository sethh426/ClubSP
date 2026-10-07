"""Address-first research and durable daily, zero-cost property watches."""
import json
import re
import time
from uuid import uuid4
from .evidence_compiler import canonical, label, records
from .meta_sentry_service import _compatible_schema
from .meta_source_transport import arcgis_sample, fetch_source
from .schema import assert_component_compatible, ensure_component

SOURCE='allen_comparable_parcels'
ADDRESS='AssessorParcel.dbo.ParcelInfo.LocationAddress'
IDENTITY='AssessorParcel.dbo.ParcelInfo.UnformattedStateKey'
VALUE='AssessorParcel.dbo.ParcelInfo.TotalAV'

class PropertyAssistantMixin:
    def _initialize_property_assistant(self):
        with self.database.session(write=True) as (c,_):
            assert_component_compatible(c,'property_assistant')
            c.execute('''CREATE TABLE IF NOT EXISTS property_watches (
                id TEXT PRIMARY KEY, subject TEXT NOT NULL UNIQUE, address TEXT NOT NULL,
                enabled INTEGER NOT NULL, next_due REAL NOT NULL, lease_until REAL NOT NULL,
                result_json TEXT NOT NULL)''')
            ensure_component(c,'property_assistant')

    def _property_source(self):
        try: row=self._active_source(SOURCE)
        except LookupError:
            raise ValueError('The county connection is unavailable. Saved research remains available; no source is automatically reactivated.') from None
        with self.database.session() as (c,_):
            p=c.execute('SELECT * FROM evidence_profiles WHERE sentra_id=?',(SOURCE,)).fetchone()
        if not p or p['activation']!=row['activated_at']:
            raise ValueError('The county source needs a reviewed mapping before address research.')
        policy=json.loads(p['body'])
        fields=json.loads(row['probe_json'])['fields']
        if (policy['identity_field']!=IDENTITY or policy['cost_cents']!=0 or
                policy['field_map'].get('parcel_identity') != [IDENTITY] or
                policy['field_map'].get('assessment') != [VALUE] or
                any(f not in fields for f in (ADDRESS,IDENTITY,VALUE))):
            raise ValueError('Address research is unavailable with this source mapping.')
        return row,policy

    def property_search(self,data):
        address=label(data.get('address'),'Street address',160).upper()
        words=re.findall(r'[A-Z0-9]+',address)
        if len(words)<2 or not words[0].isdigit() or any(ch in address for ch in '%_;\\'):
            raise ValueError('Enter a house number and street, for example 123 Main. No parcel number needed.')
        row,_=self._property_source()
        schema,metadata=self._sample_candidate(row)
        if not _compatible_schema(json.loads(row['probe_json']),schema):
            raise ValueError('County schema changed; source review is required.')
        where=' AND '.join(ADDRESS+" LIKE '%"+word+"%'" for word in words[:8])
        _,payload=arcgis_sample(row['source_url'],schema,metadata,fetch=fetch_source,where=where)
        if self._active_source(SOURCE)['activated_at']!=row['activated_at']:
            raise ValueError('Source approval changed during search.')
        matches={}
        for item in records(payload):
            subject=str(item.get(IDENTITY) or '').strip()
            if subject and item.get(ADDRESS): matches[subject]={'subject':subject,'address':str(item[ADDRESS])}
        return {'matches':list(matches.values()),'limited':len(records(payload))==25,
                'coverage_note':'Connected Allen County comparable-property records only—not every county property. No match does not mean the property does not exist.'}

    def _property_request(self,subject):
        _,policy=self._property_source()
        goals=[g for g in ('parcel_identity','assessment') if g in policy['field_map']]
        if not goals: raise ValueError('No supported property evidence mapping is available.')
        return {'subject':label(subject,'Property identity'),'jurisdiction':'Allen County, Indiana',
            'capabilities':goals,'threshold':policy['confidence'],'max_calls':1,'max_cost_cents':0,
            'max_age_hours':23,'request_key':str(uuid4())}

    def _property_summary(self,run):
        values,address,sources=[],None,[]
        with self.database.session() as (c,_):
            for sid in run.get('snapshot_ids',[]):
                s=c.execute('SELECT * FROM evidence_snapshots WHERE id=? AND sentra_id=?',(sid,SOURCE)).fetchone()
                if not s: continue
                for item in records(json.loads(s['payload_json'])):
                    if str(item.get(IDENTITY,'')).strip()==run['request']['subject']:
                        address=str(item.get(ADDRESS) or '')
                        if item.get(VALUE) is not None: values.append(item[VALUE])
                sources.append({'url':s['source_url'],'retrieved_at':s['retrieved_at']})
        return {'status':run['status'],'message':{'sufficient':'County evidence collected.',
            'incomplete':'Some county evidence is unavailable. No missing facts were guessed.',
            'review_required':'Conflicting evidence needs review.'}.get(run['status'],'Research is still processing.'),
            'address':address,'assessed_value':values[0] if len(set(map(str,values)))==1 else None,
            'sources':sources,'run_id':run['id'],'facts_imported':False,
            'note':'Reported tax assessment—not market value, an appraisal, or a guaranteed deal.'}

    def property_research(self,data):
        req=self._property_request(data.get('subject'));req['request_key']=data.get('request_key')
        run=self.evidence_run(req);result=self._property_summary(run)
        if data.get('watch') is True and run['status']=='sufficient':
            with self.database.session(write=True) as (c,_):
                existing=c.execute('SELECT enabled FROM property_watches WHERE subject=?',(req['subject'],)).fetchone()
                if c.execute('SELECT COUNT(*) FROM property_watches WHERE enabled=1').fetchone()[0]>=25 and (not existing or not existing['enabled']):
                    raise ValueError('Watch limit reached. Stop a property before adding another.')
                c.execute('''INSERT INTO property_watches VALUES(?,?,?,?,?,?,?) ON CONFLICT(subject)
                    DO UPDATE SET enabled=1,address=excluded.address,result_json=excluded.result_json''',
                    (str(uuid4()),req['subject'],result['address'] or req['subject'],1,time.time()+86400,0,canonical(result)))
            result['watching']=True
        return result

    def property_watches(self):
        with self.database.session() as (c,_):
            return {'watches':[{'id':r['id'],'address':r['address'],'enabled':bool(r['enabled']),
                'next_due':r['next_due'],'result':json.loads(r['result_json'])}
                for r in c.execute('SELECT * FROM property_watches ORDER BY address')]}

    def property_watch_stop(self,data):
        with self.database.session(write=True) as (c,_):
            c.execute('UPDATE property_watches SET enabled=0 WHERE id=?',(label(data.get('id'),'Watch'),))
        return {'stopped':True}

    def property_watch_cycle(self):
        now=time.time()
        with self.database.session(write=True) as (c,_):
            r=c.execute('SELECT * FROM property_watches WHERE enabled=1 AND next_due<=? AND lease_until<=? ORDER BY next_due LIMIT 1',(now,now)).fetchone()
            if not r:return {'checked':0}
            token=now+1800
            c.execute('UPDATE property_watches SET lease_until=?,next_due=? WHERE id=?',(token,now+86400,r['id']))
        try: result=self._property_summary(self.evidence_run(self._property_request(r['subject'])))
        except (ValueError,LookupError,OSError):
            result={'status':'unavailable','message':'County source unavailable or mapping changed. No automatic source activation.'}
        with self.database.session(write=True) as (c,_):
            c.execute('UPDATE property_watches SET lease_until=0,result_json=? WHERE id=? AND lease_until=?',(canonical(result),r['id'],token))
        return {'checked':1}
