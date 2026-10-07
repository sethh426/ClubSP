"""Isolated source/rule comparisons; shadow results cannot become production facts."""
from __future__ import annotations

import json
import math
import time
from uuid import UUID, uuid4

from .evidence_compiler import canonical, digest, integer, label, normalize, records, score
from .meta_sentry_service import _compatible_schema, _fingerprint
from .meta_source_transport import fetch_source, arcgis_sample
from .schema import assert_component_compatible, ensure_component


def predicates(value, fields):
    value = [] if value is None else value
    if not isinstance(value, list) or len(value) > 8: raise ValueError('rules must be a list of at most eight predicates')
    for rule in value:
        if (not isinstance(rule, dict) or rule.get('field') not in fields or
                rule.get('op') not in {'present','eq','gte','lte'}):
            raise ValueError('rule field/operator is not supported by the probed schema')
        if rule['op'] in {'gte','lte'}:
            number = rule.get('value')
            if type(number) not in (float,int) or not math.isfinite(number): raise ValueError('numeric rules require a finite value')
        elif rule['op'] == 'eq' and type(rule.get('value')) not in (str,int,float,bool):
            raise ValueError('equality rules require a scalar value')
    return value


def rule_matches(row, rules):
    for rule in rules:
        value = row.get(rule['field'])
        if value is None or value == '': return False
        if rule['op']=='eq' and value != rule['value']: return False
        if rule['op'] in {'gte','lte'}:
            if type(value) not in (float,int): return False
            if rule['op']=='gte' and value < rule['value']: return False
            if rule['op']=='lte' and value > rule['value']: return False
    return True


def comparable(claims):
    # Reviewed mapping order defines the semantic components of a capability.
    return {cap:sorted([list(v.values()) for v in values],key=canonical) for cap,values in claims.items()}


def metrics(payload, policy, subjects, rules):
    raw = records(payload)
    filtered = [r for r in raw if rule_matches(r,rules)]
    normalized = {subject:normalize(filtered,policy,subject) for subject in subjects}
    covered = sum(bool(n['claims']) for n in normalized.values())
    cap_hits = sum(len(n['claims']) for n in normalized.values())
    matched = [r for r in filtered if str(r.get(policy['identity_field'],'')).strip() in subjects]
    duplicates = len(matched) - len({canonical(r) for r in matched})
    return {'sample_records':len(raw),'retained_records':len(filtered),'hit_count':len(matched),
            'covered_subjects':covered,'requested_subjects':len(subjects),
            'capability_hits':cap_hits,'duplicate_hits':duplicates,
            'coverage':covered / len(subjects),'policy_weight':policy['confidence'],
            'false_hits':None,'accepted_evidence':None,'downstream_successes':None,
            'normalized':normalized}


class ShadowIntelligenceMixin:
    def _initialize_shadow(self):
        with self.database.session(write=True) as (c,_):
            assert_component_compatible(c,'shadow_intelligence')
            c.execute('''CREATE TABLE IF NOT EXISTS shadow_experiments (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, body TEXT NOT NULL,
                version TEXT NOT NULL, created_at REAL NOT NULL
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS shadow_trials (
                id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES shadow_experiments(id),
                request_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL, request_json TEXT NOT NULL,
                status TEXT NOT NULL, cost_cents INTEGER NOT NULL, result_json TEXT NOT NULL,
                started_at REAL NOT NULL, finished_at REAL, lease_until REAL NOT NULL
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS shadow_outputs (
                id TEXT PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES shadow_trials(id),
                arm TEXT NOT NULL, source_url TEXT NOT NULL, payload_hash TEXT NOT NULL,
                raw_hash TEXT NOT NULL, payload_json TEXT NOT NULL, retrieved_at REAL NOT NULL,
                UNIQUE(trial_id,arm)
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS shadow_reviews (
                id TEXT PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES shadow_trials(id),
                body TEXT NOT NULL, created_at REAL NOT NULL
            )''')
            ensure_component(c,'shadow_intelligence')

    def _shadow_candidate(self, fingerprint):
        with self.database.session() as (c,_):
            row = c.execute('SELECT * FROM meta_source_candidates WHERE fingerprint=?', (fingerprint,)).fetchone()
        if not row: raise LookupError('shadow candidate not found')
        if row['state'] not in {'schema_probed','proposed','approved','active'}:
            raise ValueError('shadow candidate needs a successful public structured probe')
        self._reviewable(row)
        if row['discovery_provider']=='apify_store': raise ValueError('Actor execution requires a dedicated adapter')
        return dict(row)

    def shadow_experiment(self, data):
        if data.get('owner_reviewed') is not True:
            raise ValueError('shadow mapping, access and budgets require owner_reviewed=true')
        baseline = self._active_source(data.get('baseline_sentra_id'))
        with self.database.session() as (c,_):
            profile = c.execute('SELECT * FROM evidence_profiles WHERE sentra_id=? AND activation=?', (baseline['id'],baseline['activated_at'])).fetchone()
        if not profile: raise ValueError('baseline needs a reviewed evidence profile')
        challenger = self._shadow_candidate(_fingerprint(data.get('challenger_fingerprint')))
        baseline_policy = json.loads(profile['body'])
        fields = set(json.loads(challenger['probe_json'])['fields'])
        mapping = data.get('field_map')
        if not isinstance(mapping,dict) or set(mapping)!=set(baseline_policy['field_map']):
            raise ValueError('challenger field_map must match baseline capability names')
        for cap, mapped in mapping.items():
            if (not isinstance(mapped,list) or len(mapped)!=len(baseline_policy['field_map'][cap]) or
                    any(not isinstance(f,str) or f not in fields for f in mapped) or len(set(mapped))!=len(mapped)):
                raise ValueError('challenger mapped components must match approved baseline arity and probed fields')
        identity = label(data.get('identity_field'),'identity_field')
        if identity not in fields: raise ValueError('identity_field must be a probed field')
        rules = predicates(data.get('rules'),fields)
        baseline_fields = set(json.loads(baseline['probe_json'])['fields'])
        baseline_rules = predicates(data.get('baseline_rules'),baseline_fields)
        challenger_policy = {'identity_field':identity,'field_map':mapping,'confidence':score(data.get('confidence',baseline_policy['confidence'])),
                             'cost_cents':integer(data.get('cost_cents',0),'cost_cents',0,100000)}
        buyer_fields = data.get('buyer_fields',{})
        if not isinstance(buyer_fields,dict) or set(buyer_fields)-{'city','state','property_type','price','repairs'}:
            raise ValueError('buyer_fields has unsupported semantic fields')
        if buyer_fields and set(buyer_fields)!={'city','state','property_type','price','repairs'}:
            raise ValueError('buyer matching requires city, state, property_type, price and repairs mappings')
        for arm in ('baseline','challenger'):
            available = baseline_fields if arm=='baseline' else fields
            if any(not isinstance(v,dict) or set(v)!={'baseline','challenger'} or v[arm] not in available for v in buyer_fields.values()):
                raise ValueError('buyer mappings must specify schema fields for both arms')
        strategy = data.get('strategy')
        if buyer_fields and strategy not in {'assignment','resale'}: raise ValueError('buyer criteria comparison requires a strategy')
        body = {'baseline_sentra_id':baseline['id'],'baseline_activation':baseline['activated_at'],
                'baseline_profile_version':profile['version'],'baseline_policy':baseline_policy,
                'baseline_schema':baseline['schema_fingerprint'],'baseline_rules':baseline_rules,
                'challenger_fingerprint':challenger['fingerprint'],'challenger_schema':challenger['schema_fingerprint'],
                'challenger_policy':challenger_policy,'rules':rules,'buyer_fields':buyer_fields,'strategy':strategy,
                'daily_trial_limit':integer(data.get('daily_trial_limit',3),'daily_trial_limit',1,12),
                'daily_cost_limit_cents':integer(data.get('daily_cost_limit_cents',0),'daily_cost_limit_cents',0,1000000),
                'note':label(data.get('note'),'note',4000)}
        experiment_id, name, version = str(uuid4()), label(data.get('name'),'name'), digest(body)
        with self.database.session(write=True) as (c,_):
            c.execute('INSERT INTO shadow_experiments VALUES(?,?,?,?,?)', (experiment_id,name,canonical(body),version,time.time()))
        return {'id':experiment_id,'name':name,'version':version,'production_activation':False}

    def _shadow_capture(self, row, expected_schema):
        started = time.time()
        schema,payload = self._sample_candidate(row)
        if not _compatible_schema(expected_schema,schema): raise ValueError('shadow source schema drifted')
        if schema['shape']=='arcgis_layer':
            response,payload = arcgis_sample(row['source_url'],schema,payload,fetch=fetch_source)
            schema['payload_hash']=response.payload_hash
        return {'payload':payload,'payload_hash':digest(payload),'raw_hash':schema['payload_hash'],
                'source_url':row['source_url'],'duration_ms':max(0,int((time.time()-started)*1000))}

    def _shadow_buyer_matches(self, payload, fields, arm, strategy, subjects, identity_field, rules):
        if not fields: return None
        with self.database.session() as (c,_):
            buyers = [dict(b) for b in c.execute("SELECT * FROM buyers WHERE status='active'")]
        matches = set()
        for row in records(payload):
            subject = str(row.get(identity_field,'')).strip()
            if subject not in subjects or not rule_matches(row,rules): continue
            values = {semantic:row.get(mapping[arm]) for semantic,mapping in fields.items()}
            price,repairs = values['price'],values['repairs']
            if (any(values[k] is None or values[k]=='' for k in values) or
                    type(price) not in (float,int) or type(repairs) not in (float,int) or
                    price<0 or repairs<0): continue
            location = f"{values['city']}, {values['state']}".strip().lower()
            for buyer in buyers:
                if (strategy in json.loads(buyer['strategies_json']) and
                        location in [v.strip().lower() for v in json.loads(buyer['locations_json'])] and
                        (not json.loads(buyer['property_types_json']) or values['property_type'] in json.loads(buyer['property_types_json'])) and
                        price<=buyer['max_total_price'] and repairs<=buyer['max_repairs']):
                    matches.add((subject,buyer['id']))
        return [{'subject':s,'buyer_id':b} for s,b in sorted(matches)]

    def shadow_trial(self, data):
        experiment_id = label(data.get('experiment_id'),'experiment_id')
        subjects = data.get('subjects')
        if not isinstance(subjects,list) or not 1<=len(subjects)<=25:
            raise ValueError('subjects must contain 1-25 exact record identities')
        subjects = sorted({label(s,'subject') for s in subjects})
        budget = integer(data.get('max_cost_cents',0),'max_cost_cents',0,1000000)
        try: key = str(UUID(str(data.get('request_key'))))
        except (ValueError,TypeError): raise ValueError('request_key must be a UUID') from None
        request = {'experiment_id':experiment_id,'subjects':subjects,'max_cost_cents':budget}
        now, request_hash = time.time(), digest(request)
        with self.database.session(write=True) as (c,_):
            existing = c.execute('SELECT * FROM shadow_trials WHERE request_key=?',(key,)).fetchone()
            if existing:
                if existing['request_hash']!=request_hash: raise ValueError('request_key belongs to different shadow inputs')
                if existing['status']=='running' and existing['lease_until']<now:
                    c.execute("UPDATE shadow_trials SET status='interrupted',finished_at=? WHERE id=?",(now,existing['id']))
                    return {'id':existing['id'],'status':'interrupted','retry_requires_new_key':True}
                return {'id':existing['id'],'status':existing['status'],**json.loads(existing['result_json'])}
            experiment = c.execute('SELECT * FROM shadow_experiments WHERE id=?',(experiment_id,)).fetchone()
            if not experiment: raise LookupError('shadow experiment not found')
            body = json.loads(experiment['body'])
            cost = body['baseline_policy']['cost_cents']+body['challenger_policy']['cost_cents']
            count,total = c.execute('SELECT count(*),coalesce(sum(cost_cents),0) FROM shadow_trials WHERE experiment_id=? AND started_at>?',(experiment_id,now-86400)).fetchone()
            if cost>budget or count>=body['daily_trial_limit'] or total+cost>body['daily_cost_limit_cents']:
                raise ValueError('shadow acquisition budget exhausted')
            trial_id = str(uuid4())
            c.execute('INSERT INTO shadow_trials VALUES(?,?,?,?,?,?,?,?,?,?,?)', (trial_id,experiment_id,key,request_hash,canonical(request),'running',cost,'{}',now,None,now+1800))
        arms, captures = {}, {}
        for arm in ('baseline','challenger'):
            try:
                if arm=='baseline':
                    row = self._active_source(body['baseline_sentra_id'])
                    with self.database.session() as (c,_):
                        profile = c.execute('SELECT version FROM evidence_profiles WHERE sentra_id=?',(row['id'],)).fetchone()
                    if (row['activated_at']!=body['baseline_activation'] or not profile or profile[0]!=body['baseline_profile_version'] or
                            row['schema_fingerprint']!=body['baseline_schema']): raise ValueError('baseline approval/profile changed; create a new experiment')
                    policy, rules = body['baseline_policy'], body['baseline_rules']
                else:
                    row = self._shadow_candidate(body['challenger_fingerprint'])
                    if row['schema_fingerprint']!=body['challenger_schema']: raise ValueError('challenger probe changed; create a new experiment')
                    policy, rules = body['challenger_policy'], body['rules']
                capture = self._shadow_capture(row,json.loads(row['probe_json']))
                # Fence changes during acquisition; do not mutate either source.
                current = self._active_source(row['id']) if arm=='baseline' else self._shadow_candidate(row['fingerprint'])
                if current['schema_fingerprint']!=row['schema_fingerprint']: raise ValueError('source schema approval changed during trial')
                if arm=='baseline' and current['activated_at']!=row['activated_at']: raise ValueError('baseline activation changed during trial')
                if arm=='baseline':
                    with self.database.session() as (c,_):
                        current_profile=c.execute('SELECT version FROM evidence_profiles WHERE sentra_id=?',(row['id'],)).fetchone()
                    if not current_profile or current_profile[0]!=body['baseline_profile_version']:
                        raise ValueError('baseline profile changed during trial')
                result = metrics(capture['payload'],policy,subjects,rules)
                result['duration_ms'], result['cost_cents'], result['schema_errors'] = capture['duration_ms'], policy['cost_cents'], 0
                result['buyer_criteria_matches'] = self._shadow_buyer_matches(capture['payload'],body['buyer_fields'],arm,body['strategy'],subjects,policy['identity_field'],rules)
                result['status']='success'
                with self.database.session(write=True) as (c,_):
                    c.execute('INSERT INTO shadow_outputs VALUES(?,?,?,?,?,?,?,?)',(str(uuid4()),trial_id,arm,capture['source_url'],capture['payload_hash'],capture['raw_hash'],canonical(capture['payload']),time.time()))
                arms[arm], captures[arm] = result, capture
            except Exception as exc:
                arms[arm] = {'status':'failed','error':type(exc).__name__+'; inspect source/schema before retry',
                             'schema_errors':int(isinstance(exc,ValueError)),'cost_cents':body[arm+'_policy']['cost_cents'],
                             'false_hits':None,'downstream_successes':None}
        differences = []
        if all(arms[a]['status']=='success' for a in arms):
            for subject in subjects:
                left,right = (comparable(arms[a]['normalized'][subject]['claims']) for a in ('baseline','challenger'))
                if left!=right: differences.append({'subject':subject,'baseline':left,'challenger':right})
        status = 'completed' if all(arms[a]['status']=='success' for a in arms) else 'failed'
        output = {'experiment_id':experiment_id,'subjects':subjects,'arms':arms,'differences':differences,
                  'budgeted_cost_cents':cost,'production_activation':False,'facts_imported':False,
                  'outcome_review_required':True}
        with self.database.session(write=True) as (c,_):
            c.execute('UPDATE shadow_trials SET status=?,result_json=?,finished_at=?,lease_until=0 WHERE id=?',(status,canonical(output),time.time(),trial_id))
        return {'id':trial_id,'status':status,**output}

    def shadow_review(self, data):
        trial_id = label(data.get('trial_id'),'trial_id')
        decision = data.get('decision')
        if decision not in {'retain_baseline','consider_challenger','continue_shadow','reject_challenger'}:
            raise ValueError('unsupported shadow review decision')
        review = {'decision':decision,'reviewer':label(data.get('reviewer'),'reviewer'),
                  'note':label(data.get('note'),'note',4000),'evidence_reference':label(data.get('evidence_reference'),'evidence_reference',500)}
        outcomes = data.get('outcomes',{})
        if not isinstance(outcomes,dict) or set(outcomes)-{'baseline','challenger'}: raise ValueError('outcomes must label trial arms')
        with self.database.session(write=True) as (c,_):
            row = c.execute('SELECT * FROM shadow_trials WHERE id=?',(trial_id,)).fetchone()
            if not row: raise LookupError('shadow trial not found')
            if row['status'] not in {'completed','failed'}: raise ValueError('shadow trial must be finished before review')
            result = json.loads(row['result_json'])
            validated = {}
            for arm,values in outcomes.items():
                if result['arms'][arm]['status']!='success' or not isinstance(values,dict): raise ValueError('only acquired arms can have outcome labels')
                if set(values)-{'false_hits','accepted_evidence','downstream_successes'}: raise ValueError('unsupported outcome metric')
                hits = result['arms'][arm]['hit_count']
                validated[arm] = {k:integer(v,k,0,hits) for k,v in values.items()}
                if validated[arm].get('downstream_successes',0)>validated[arm].get('accepted_evidence',0): raise ValueError('downstream_successes require accepted_evidence')
                if validated[arm].get('false_hits',0)+validated[arm].get('accepted_evidence',0)>hits: raise ValueError('false and accepted hit counts exceed acquired hits')
            review['outcomes']=validated
            review_id=str(uuid4())
            c.execute('INSERT INTO shadow_reviews VALUES(?,?,?,?)',(review_id,trial_id,canonical(review),time.time()))
        return {'id':review_id,'trial_id':trial_id,**review,'production_activation':False}

    def shadow_state(self):
        with self.database.session() as (c,_):
            experiments=[{'id':r['id'],'name':r['name'],'version':r['version'],'config':json.loads(r['body'])} for r in c.execute('SELECT * FROM shadow_experiments ORDER BY created_at DESC LIMIT 50')]
            trials=[{'id':r['id'],'experiment_id':r['experiment_id'],'status':r['status'],'started_at':r['started_at'],'result':json.loads(r['result_json'])} for r in c.execute('SELECT * FROM shadow_trials ORDER BY started_at DESC LIMIT 100')]
            reviews=[{'id':r['id'],'trial_id':r['trial_id'],'reviewed_at':r['created_at'],**json.loads(r['body'])} for r in c.execute('SELECT * FROM shadow_reviews ORDER BY created_at DESC LIMIT 100')]
        reports=[]
        for experiment in experiments:
            sample=[t for t in trials if t['experiment_id']==experiment['id']]
            successes=[t for t in sample if t['status']=='completed']
            report={'experiment_id':experiment['id'],'trial_count':len(sample),'successful_trials':len(successes),'recommendation':'continue_shadow','review_required':True}
            if any(t['status']=='failed' for t in sample): report['recommendation']='investigate_failures'
            elif successes:
                report['coverage_delta']=sum(t['result']['arms']['challenger']['coverage']-t['result']['arms']['baseline']['coverage'] for t in successes)/len(successes)
                report['mean_cost_delta_cents']=sum(t['result']['arms']['challenger']['cost_cents']-t['result']['arms']['baseline']['cost_cents'] for t in successes)/len(successes)
                report['mean_latency_delta_ms']=sum(t['result']['arms']['challenger']['duration_ms']-t['result']['arms']['baseline']['duration_ms'] for t in successes)/len(successes)
                report['duplicate_delta']=sum(t['result']['arms']['challenger']['duplicate_hits']-t['result']['arms']['baseline']['duplicate_hits'] for t in successes)
                labeled=[]
                for trial in successes:
                    last=next((r for r in reviews if r['trial_id']==trial['id']),None)
                    if last and all(set(last['outcomes'].get(a,{}))=={'false_hits','accepted_evidence','downstream_successes'} for a in ('baseline','challenger')):
                        labeled.append(last)
                report['outcome_labeled_trials']=len(labeled)
                report['recommendation']='needs_outcome_review'
                if len(labeled)>=3:
                    false_delta=sum(r['outcomes']['challenger']['false_hits']-r['outcomes']['baseline']['false_hits'] for r in labeled)
                    quality_delta=sum(r['outcomes']['challenger']['accepted_evidence']-r['outcomes']['baseline']['accepted_evidence'] for r in labeled)
                    outcome_delta=sum(r['outcomes']['challenger']['downstream_successes']-r['outcomes']['baseline']['downstream_successes'] for r in labeled)
                    report.update({'false_hit_delta':false_delta,'accepted_evidence_delta':quality_delta,'downstream_success_delta':outcome_delta})
                    eligible=report['coverage_delta']>=0 and report['mean_cost_delta_cents']<=0 and report['duplicate_delta']<=0 and false_delta<=0 and quality_delta>=0 and outcome_delta>=0
                    report['recommendation']='review_challenger' if eligible else 'retain_baseline'
            reports.append(report)
        return {'experiments':experiments,'trials':trials,'reviews':reviews,'reports':reports,'automatic_promotion':False}
