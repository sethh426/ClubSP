from uuid import uuid4
import pytest
from app.service import Application
from app.temporal_intelligence import signatures, change_details
from tests.test_evidence_compiler import setup, request


def observe(app, name, payload, now):
    row = app._active_source(name)
    return app.temporal_observe_result(row, {'payload':payload,'payload_sha256':'a'*64,
        'metadata':{'raw_payload_hash':'b'*64},'source_url':row['source_url'],'duration_ms':50}, now)


def policy(app, name, **extra):
    return app.temporal_policy({'sentra_id':name,'owner_reviewed':True,'note':'Isolated test schedule',**extra})


def test_signatures_ignore_row_order_and_key_order_but_detect_values():
    a = [{'id':'A','value':1},{'id':'B','value':2}]
    assert signatures(a,'id') == signatures(list(reversed(a)),'id')
    assert signatures(a,'id') != signatures([{'id':'A','value':2},{'id':'B','value':2}],'id')
    diff = change_details(signatures(a,'id'),signatures([{'id':'A','value':2}],'id'))
    assert diff['changed_fields'] == {'A':['value']}
    assert diff['absent_from_sample'] == ['B'] and diff['sample_complete'] is False


def test_baseline_stable_changed_and_adaptive_bounds(setup):
    app, add, _, _ = setup
    add('one',['parcel_identity','assessment'])
    policy(app,'one',base_interval_seconds=14400)
    payload = [{'parcel_id':'A-1','assessment':100}]
    now = 2_000_000_000
    assert observe(app,'one',payload,now)['kind'] == 'baseline'
    for i in range(1,4): observe(app,'one',payload,now+i*3600)
    assert app.temporal_state()['sources'][0]['interval_seconds'] == 21600
    changed = observe(app,'one',[{'parcel_id':'A-1','assessment':200}],now+4*3600)
    assert changed['kind'] == 'changed' and changed['interval_seconds'] == 10800
    for i in range(5,12):
        observe(app,'one',[{'parcel_id':'A-1','assessment':i*100}],now+i*3600)
    state = app.temporal_state()
    assert 3600 <= state['sources'][0]['interval_seconds'] <= 86400
    assert state['sources'][0]['ewma_change_seconds'] is not None
    assert any(e['details'].get('changed_fields') == {'A-1':['assessment']} for e in state['events'])


def test_repeated_rapid_observations_do_not_teach_longer_cadence(setup):
    app, add, _, _ = setup
    add('one',['parcel_identity'])
    policy(app,'one',base_interval_seconds=3600)
    for i in range(6): observe(app,'one',[{'parcel_id':'A-1'}],2_000_000_000+i)
    assert app.temporal_state()['sources'][0]['interval_seconds'] == 3600


def test_mapping_changes_start_new_baseline_and_old_dates_are_ignored(setup):
    app, add, _, _ = setup
    add('one',['parcel_identity'])
    row = [{'parcel_id':'A-1'}]
    observe(app,'one',row,2_000_000_000)
    app.evidence_profile({'sentra_id':'one','owner_reviewed':True,'identity_field':'parcel_id',
                         'field_map':{'parcel_identity':['parcel_id']},'confidence':.6,'note':'Changed mapping policy'})
    assert observe(app,'one',row,2_000_003_600)['kind'] == 'baseline'
    assert observe(app,'one',row,1_000)['recorded'] is False
    assert app.temporal_state()['sources'][0]['changes'] == 0


def test_cycle_reservations_budget_and_restart(setup):
    app, add, _, calls = setup
    add('one',['parcel_identity'])
    policy(app,'one',daily_call_limit=1)
    first = app.temporal_cycle()
    assert first['checked'] == 1
    assert app.temporal_state()['sources'][0]['observations'] == 1
    app = Application(app.database.path)
    assert app.temporal_cycle()['checked'] == 0
    with app.database.session(write=True) as (c, _):
        c.execute('UPDATE temporal_sources SET next_due=0')
    second = app.temporal_cycle()
    assert second['checked'] == 0 and second['blocked'][0]['status'] == 'budget_limited'
    with app.database.session() as (c, _):
        assert c.execute('SELECT count(*) FROM temporal_attempts').fetchone()[0] == 1
    assert not app.state()['facts']


def test_lease_excludes_another_process_and_policy_disabled_excludes_calls(setup):
    app, add, _, calls = setup
    add('one',['parcel_identity'])
    policy(app,'one')
    with app.database.session(write=True) as (c, _):
        c.execute('UPDATE temporal_sources SET lease_until=3000000000')
    calls.clear()
    assert Application(app.database.path).temporal_cycle()['checked'] == 0 and not calls
    policy(app,'one',enabled=False)
    with app.database.session(write=True) as (c, _): c.execute('UPDATE temporal_sources SET lease_until=0')
    assert app.temporal_cycle()['checked'] == 0


def test_failure_retains_budget_and_requarantine_is_not_reactivated(setup):
    app, add, payloads, _ = setup
    add('one',['parcel_identity'])
    policy(app,'one',cost_cents=5,daily_cost_limit_cents=5)
    payloads['one.example.gov']=503
    result=app.temporal_cycle()
    assert result['results'][0]['status']=='failed'
    with app.database.session() as (c, _):
        row=c.execute('SELECT * FROM temporal_attempts').fetchone()
        assert row['status']=='failed' and row['cost_cents']==5
    assert app.meta_sentra_state()['summary']['requarantined']==1
    assert app.temporal_cycle()['checked']==0


def test_cost_limit_blocks_before_network_and_freshness_bounds(setup):
    app, add, _, calls=setup
    add('one',['parcel_identity'])
    policy(app,'one',cost_cents=5,daily_cost_limit_cents=0)
    calls.clear()
    assert app.temporal_cycle()['checked']==0 and not calls
    with pytest.raises(ValueError): policy(app,'one',base_interval_seconds=100000)
    with pytest.raises(ValueError): policy(app,'one',min_interval_seconds=10)
    with pytest.raises(ValueError): app.temporal_policy({'sentra_id':'one','note':'Unreviewed'})


def test_meta_execution_records_temporal_events(setup):
    app, add, _, _=setup
    add('one',['parcel_identity'])
    app.meta_execute({'sentra_id':'one'})
    assert app.temporal_state()['events'][0]['kind']=='baseline'
    assert app.temporal_state()['sources'][0]['next_due']>0
    assert not app.temporal_state()['policies']


def test_observed_change_invalidates_compiler_cache(setup):
    app, add, payloads, _ = setup
    add('one',['parcel_identity','assessment'])
    run=app.evidence_run(request())
    assert run['status']=='sufficient'
    payloads['one.example.gov'][0]['assessment']=110000
    app.meta_execute({'sentra_id':'one'})
    assert app.temporal_state()['sources'][0]['changes']==1
    assert app.evidence_plan(request())['cached_snapshot_ids']==[]
