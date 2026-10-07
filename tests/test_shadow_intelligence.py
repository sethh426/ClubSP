import json
import threading
from uuid import uuid4
import httpx
import pytest
from app.meta_sentras import SourceCandidate
from app.server import create_server
from app.auth import OwnerAuth, SESSION_COOKIE
from app.service import Application
from tests.test_evidence_compiler import setup


@pytest.fixture
def experiment(setup):
    app,add,payloads,calls=setup
    add('baseline',['parcel_identity','assessment'],confidence=.8)
    candidate=SourceCandidate('data_gov','challenger','Example County parcel assessor',
        'https://challenger.example.gov/data.json',jurisdiction_hint='Example County',capabilities_hint=('parcel_identity','assessment'))
    payloads['challenger.example.gov']=[{'pin':'A-1','assessed':100000,'city':'Fort Wayne','state':'IN','kind':'single_family','price':90000,'repairs':10000}]
    app._store_discovered_candidates('data_gov','parcel',[candidate],'a'*64)
    app.meta_probe({'fingerprint':candidate.fingerprint})
    config={'name':'Isolated comparison','baseline_sentra_id':'baseline','challenger_fingerprint':candidate.fingerprint,
        'owner_reviewed':True,'identity_field':'pin','field_map':{'parcel_identity':['pin'],'assessment':['assessed']},
        'note':'Synthetic mapping and public access review','daily_trial_limit':6}
    exp=app.shadow_experiment(config)
    return app,candidate,exp,payloads,calls,config


def trial(app,exp,**extra):
    return app.shadow_trial({'experiment_id':exp['id'],'subjects':['A-1'],'request_key':str(uuid4()),**extra})


def test_shadow_capture_and_different_schema_mapping_are_isolated(experiment):
    app,candidate,exp,_,_,_=experiment
    before=app.meta_sentra_state()
    run=trial(app,exp)
    assert run['status']=='completed' and run['differences']==[]
    assert run['arms']['challenger']['coverage']==1 and run['arms']['challenger']['schema_errors']==0
    assert run['arms']['challenger']['false_hits'] is None
    assert app.meta_sentra_state()['summary']==before['summary']
    assert next(c for c in app.meta_sentra_state()['candidates'] if c['fingerprint']==candidate.fingerprint)['state']=='schema_probed'
    assert app.evidence_state()['runs']==[] and app.temporal_state()['events']==[]
    with app.database.session() as(c,memory):
        assert not memory.facts and c.execute('SELECT count(*) FROM shadow_outputs').fetchone()[0]==2
    assert not run['production_activation'] and not run['facts_imported']


def test_rule_variant_changes_coverage_without_downstream_writes(experiment):
    app,_,_,_,_,config=experiment
    exp=app.shadow_experiment({**config,'rules':[{'field':'assessed','op':'gte','value':200000}]})
    run=trial(app,exp)
    assert run['arms']['baseline']['covered_subjects']==1
    assert run['arms']['challenger']['covered_subjects']==0
    assert run['differences'][0]['subject']=='A-1'
    assert app.shadow_state()['reports'][0]['recommendation']=='needs_outcome_review'


def test_duplicate_hits_and_unknown_outcomes(experiment):
    app,_,exp,payloads,_,_=experiment
    payloads['challenger.example.gov']*=2
    run=trial(app,exp)
    assert run['arms']['challenger']['hit_count']==2 and run['arms']['challenger']['duplicate_hits']==1
    assert run['arms']['challenger']['downstream_successes'] is None


def test_schema_failure_cannot_suspend_baseline_or_activate_challenger(experiment):
    app,candidate,exp,payloads,_,_=experiment
    payloads['challenger.example.gov']=[{'unexpected':'schema'}]
    run=trial(app,exp)
    assert run['status']=='failed' and run['arms']['challenger']['schema_errors']==1
    assert app._active_source('baseline')['state']=='active'
    assert app.meta_sentra_state()['summary']['active']==1
    assert app.shadow_state()['reports'][0]['recommendation']=='investigate_failures'


def test_budget_blocks_before_network_and_failures_stay_reserved(experiment):
    app,_,_,payloads,calls,config=experiment
    exp=app.shadow_experiment({**config,'cost_cents':10,'daily_cost_limit_cents':10})
    calls.clear()
    with pytest.raises(ValueError,match='budget'):trial(app,exp)
    assert not calls
    payloads['challenger.example.gov']=503
    run=trial(app,exp,max_cost_cents=10)
    assert run['budgeted_cost_cents']==10
    with pytest.raises(ValueError,match='budget'):trial(app,exp,max_cost_cents=10)


def test_replay_and_request_collision_survive_restart(experiment):
    app,_,exp,_,calls,_=experiment
    data={'experiment_id':exp['id'],'subjects':['A-1'],'request_key':str(uuid4())}
    first=app.shadow_trial(data)
    calls.clear()
    app=Application(app.database.path)
    assert app.shadow_trial(data)==first and not calls
    with pytest.raises(ValueError,match='different shadow inputs'):
        app.shadow_trial({**data,'subjects':['B-2']})
    with app.database.session(write=True) as(c,_):
        c.execute("UPDATE shadow_trials SET status='running',lease_until=0 WHERE id=?",(first['id'],))
    assert app.shadow_trial(data)['status']=='interrupted' and not calls


def test_review_outcomes_enable_advisory_replacement_report_only(experiment):
    app,_,exp,_,_,_=experiment
    for i in range(3):
        run=trial(app,exp)
        app.shadow_review({'trial_id':run['id'],'decision':'consider_challenger','reviewer':'Synthetic reviewer',
            'note':'Synthetic outcome evidence','evidence_reference':f'fixture:{i}',
            'outcomes':{a:{'false_hits':0,'accepted_evidence':1,'downstream_successes':1} for a in ('baseline','challenger')}})
    state=app.shadow_state()
    assert state['reports'][0]['recommendation']=='review_challenger'
    assert state['reports'][0]['outcome_labeled_trials']==3
    assert state['automatic_promotion'] is False and app.meta_sentra_state()['summary']['active']==1


def test_invalid_outcome_labels_and_unreviewed_mappings(experiment):
    app,_,exp,_,_,config=experiment
    with pytest.raises(ValueError,match='owner_reviewed'):app.shadow_experiment({**config,'owner_reviewed':False})
    with pytest.raises(ValueError,match='mapped'):app.shadow_experiment({**config,'field_map':{'assessment':['guessed'],'parcel_identity':['pin']}})
    run=trial(app,exp)
    data={'trial_id':run['id'],'decision':'continue_shadow','reviewer':'Example','note':'Test','evidence_reference':'fixture'}
    with pytest.raises(ValueError):app.shadow_review({**data,'outcomes':{'challenger':{'false_hits':2}}})
    with pytest.raises(ValueError):app.shadow_review({**data,'outcomes':{'challenger':{'downstream_successes':1}}})


def test_profile_version_change_requires_new_experiment(experiment):
    app,_,exp,_,_,_=experiment
    app.evidence_profile({'sentra_id':'baseline','owner_reviewed':True,'identity_field':'parcel_id',
        'field_map':{'parcel_identity':['parcel_id'],'assessment':['assessment']},'note':'New version','confidence':.9})
    assert trial(app,exp)['arms']['baseline']['status']=='failed'


def test_rejected_and_unprobed_sources_cannot_be_shadowed(experiment):
    app,candidate,_,_,_,config=experiment
    app.meta_review({'fingerprint':candidate.fingerprint,'decision':'reject','note':'Rejected for test'})
    with pytest.raises(ValueError,match='probe'):app.shadow_experiment(config)


def test_intelligence_http_routes_require_auth_and_origin(experiment):
    app,_,_,_,_,_=experiment
    auth=OwnerAuth(secret='synthetic-owner-secret-123456')
    server=create_server(app.database.path,port=0,application=app,auth=auth)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    origin='http://127.0.0.1:'+str(server.server_address[1])
    try:
        with httpx.Client(base_url=origin,trust_env=False) as client:
            gets=['evidence','temporal','shadow']
            posts=['evidence/profile','evidence/plan','evidence/run','temporal/policy','temporal/cycle','shadow/experiment','shadow/trial','shadow/review']
            for p in gets:assert client.get('/api/sentras/'+p).status_code==401
            for p in posts:assert client.post('/api/sentras/'+p,json={},headers={'Origin':origin}).status_code==401
            client.cookies.set(SESSION_COOKIE,auth.issue())
            for p in posts:
                assert client.post('/api/sentras/'+p,json={}).status_code==403
                assert client.post('/api/sentras/'+p,json={},headers={'Origin':'http://foreign.invalid'}).status_code==403
            for p in gets:assert client.get('/api/sentras/'+p).status_code==200
    finally:server.shutdown();server.server_close();worker.join(timeout=2)


def test_recorded_buyer_criteria_are_compared_without_saving_matches(experiment):
    app,_,_,payloads,_,config=experiment
    payloads['baseline.example.gov'][0].update(city='Fort Wayne',state='IN',kind='single_family',price=90000,repairs=10000)
    # Review the expanded baseline schema explicitly; experiments must use its
    # current approved activation and mapping rather than accepting silent drift.
    app.meta_requarantine(app._active_source('baseline')['fingerprint'],'Synthetic schema extension review')
    fingerprint=next(c['fingerprint'] for c in app.meta_sentra_state()['candidates'] if c['external_id']=='baseline')
    app.meta_probe({'fingerprint':fingerprint});app.meta_propose({'fingerprint':fingerprint})
    app.meta_review({'fingerprint':fingerprint,'decision':'approve','note':'Synthetic added fields reviewed'})
    app.meta_activate({'fingerprint':fingerprint,'sentra_id':'baseline','family':'parcel_assessor',
        'acquisition_mode':'direct_http','jurisdiction':'Example County','rights_note':'Synthetic test'})
    app.evidence_profile({'sentra_id':'baseline','owner_reviewed':True,'identity_field':'parcel_id',
        'field_map':{'parcel_identity':['parcel_id'],'assessment':['assessment']},'confidence':.8,'note':'Synthetic buyer mapping'})
    with app.database.session(write=True) as(c,_):
        buyer_id=str(uuid4())
        c.execute('INSERT INTO buyers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (buyer_id,'Fixture buyer','',json.dumps(['Fort Wayne, IN']),json.dumps(['assignment']),json.dumps(['single_family']),100000,15000,'unverified','','','active','2026-10-01'))
    fields={semantic:{'baseline':field,'challenger':field} for semantic,field in {'city':'city','state':'state','property_type':'kind','price':'price','repairs':'repairs'}.items()}
    exp=app.shadow_experiment({**config,'buyer_fields':fields,'strategy':'assignment'})
    run=trial(app,exp)
    for arm in ('baseline','challenger'):
        assert run['arms'][arm]['buyer_criteria_matches']==[{'subject':'A-1','buyer_id':buyer_id}]
    with app.database.session() as(c,_):
        assert c.execute('SELECT count(*) FROM buyer_match_runs').fetchone()[0]==0


def test_simultaneous_shadow_key_is_fenced_by_durable_running_trial(experiment,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    app,_,exp,_,_,_=experiment
    original=app._shadow_capture
    started,release=threading.Event(),threading.Event()
    def paused(*args):
        started.set(); assert release.wait(5); return original(*args)
    monkeypatch.setattr(app,'_shadow_capture',paused)
    data={'experiment_id':exp['id'],'subjects':['A-1'],'request_key':str(uuid4())}
    with ThreadPoolExecutor(max_workers=1) as pool:
        running=pool.submit(app.shadow_trial,data)
        assert started.wait(5)
        duplicate=Application(app.database.path).shadow_trial(data)
        assert duplicate['status']=='running'
        release.set()
        finished=running.result(timeout=5)
    assert duplicate['id']==finished['id'] and finished['status']=='completed'
