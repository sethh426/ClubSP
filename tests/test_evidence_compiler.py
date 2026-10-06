import json
from uuid import uuid4
import httpx
import pytest

import app.meta_sentry_service as meta
from app.meta_sentras import SourceCandidate
from app.meta_source_transport import fetch_source
from app.evidence_compiler import cheapest_sequence, normalize
from app.service import Application


@pytest.fixture
def setup(tmp_path, monkeypatch):
    app = Application(tmp_path / 'compiler.sqlite3')
    payloads, calls = {}, []
    def handle(request):
        calls.append(str(request.url))
        value = payloads[request.url.host]
        return httpx.Response(value if isinstance(value, int) else 200, json={} if isinstance(value, int) else value)
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(meta, 'fetch_source', lambda url, **kw: fetch_source(url, transport=transport, **kw))
    def add(name, caps, cost=0, requires=None, confidence=.8, payload=None):
        payloads[name + '.example.gov'] = payload or [{'parcel_id': 'A-1', 'assessment': 100000, 'owner': 'Example'}]
        candidate = SourceCandidate('data_gov', name, name, f'https://{name}.example.gov/data.json',
                                    jurisdiction_hint='Example County', capabilities_hint=tuple(caps))
        app._store_discovered_candidates('data_gov', 'parcel', [candidate], 'a'*64)
        app.meta_probe({'fingerprint': candidate.fingerprint})
        app.meta_propose({'fingerprint': candidate.fingerprint})
        app.meta_review({'fingerprint': candidate.fingerprint, 'decision': 'approve', 'note': 'Synthetic isolated test source'})
        app.meta_activate({'fingerprint': candidate.fingerprint, 'sentra_id': name, 'family': 'parcel_assessor',
                           'acquisition_mode': 'official_api', 'jurisdiction': 'Example County', 'rights_note': 'Synthetic test'})
        app.evidence_profile({'sentra_id': name, 'owner_reviewed': True, 'identity_field': 'parcel_id',
                              'field_map': {cap:[{'parcel_identity':'parcel_id', 'assessment':'assessment', 'owner_of_record':'owner'}[cap]] for cap in caps},
                              'cost_cents': cost, 'requires': requires or [], 'confidence': confidence, 'note': 'Synthetic policy'})
        return candidate
    return app, add, payloads, calls


def request(**extra):
    return {'subject':'A-1', 'jurisdiction':'Example County', 'capabilities':['parcel_identity','assessment'],
            'threshold':.7, 'max_cost_cents':20, 'request_key':str(uuid4()), **extra}


def test_exact_planner_chooses_bundle_over_greedy_singletons(setup):
    app, add, _, calls = setup
    add('identity', ['parcel_identity'], 6)
    add('assessor', ['assessment'], 6)
    add('bundle', ['parcel_identity','assessment'], 10)
    calls.clear()
    assert app.evidence_plan(request())['sequence'] == ['bundle']
    assert not calls
    run = app.evidence_run(request())
    assert run['status'] == 'sufficient' and run['calls'] == 1 and run['budgeted_cost_cents'] == 10
    assert run['scores'] == {'assessment':.8, 'parcel_identity':.8}
    with app.database.session() as (_, memory):
        assert not memory.facts


def test_dependencies_and_cycle_blocking(setup):
    app, add, _, _ = setup
    add('identity', ['parcel_identity'], 1)
    add('assessor', ['assessment'], 1, requires=['parcel_identity'])
    assert app.evidence_plan(request())['sequence'] == ['identity','assessor']
    assert app.evidence_run(request())['status'] == 'sufficient'
    with app.database.session(write=True) as (c, _):
        c.execute('DELETE FROM evidence_snapshots')
    app.evidence_profile({'sentra_id':'identity', 'owner_reviewed':True, 'identity_field':'parcel_id',
                         'field_map':{'parcel_identity':['parcel_id']}, 'requires':['assessment'], 'confidence':.8, 'note':'Cycle test'})
    assert not app.evidence_plan(request())['satisfiable']
    assert app.evidence_run(request())['calls'] == 0


def test_budget_and_call_limit_are_enforced(setup):
    app, add, _, calls = setup
    add('identity', ['parcel_identity'], 10)
    add('assessor', ['assessment'], 10, requires=['parcel_identity'])
    calls.clear()
    run = app.evidence_run(request(max_cost_cents=10))
    assert run['status'] == 'incomplete' and run['budgeted_cost_cents'] <= 10 and run['calls'] <= 1
    run = app.evidence_run(request(max_calls=1))
    assert run['calls'] <= 1


def test_real_rows_not_schema_or_different_parcel_determine_coverage(setup):
    app, add, payloads, _ = setup
    add('other', ['parcel_identity','assessment'], payload=[{'parcel_id':'B-2','assessment':200000,'owner':'Other'}])
    run = app.evidence_run(request())
    assert run['status'] == 'incomplete' and run['scores'] == {}
    payloads['other.example.gov'] = []
    assert normalize([], {'identity_field':'parcel_id', 'field_map':{'assessment':['assessment']}}, 'A-1')['claims'] == {}


def test_cache_survives_restart_and_stops_calls(setup):
    app, add, _, calls = setup
    add('bundle', ['parcel_identity','assessment'], 10)
    first = app.evidence_run(request())
    calls.clear()
    app = Application(app.database.path)
    second = app.evidence_run(request(max_cost_cents=0))
    assert second['status'] == 'sufficient' and second['calls'] == 0 and not calls
    assert second['snapshot_ids'] == first['snapshot_ids']
    with app.database.session(write=True) as (c, _):
        c.execute('UPDATE evidence_snapshots SET retrieved_at=0')
    assert not app.evidence_plan(request(max_cost_cents=0))['satisfiable']


def test_key_replay_and_mismatch(setup):
    app, add, _, calls = setup
    add('bundle', ['parcel_identity','assessment'])
    data = request()
    first = app.evidence_run(data)
    calls.clear()
    assert app.evidence_run(data) == first and not calls
    with pytest.raises(ValueError, match='different inputs'):
        app.evidence_run({**data,'subject':'B-2'})


def test_interrupted_key_does_not_repeat_acquisition(setup):
    app, add, _, calls = setup
    add('bundle', ['parcel_identity','assessment'])
    data = request()
    first = app.evidence_run(data)
    with app.database.session(write=True) as (c, _):
        c.execute("UPDATE evidence_runs SET status='running',lease_until=0 WHERE id=?", (first['id'],))
    calls.clear()
    assert app.evidence_run(data)['status'] == 'interrupted' and not calls


def test_failure_reserves_budget_and_replans(setup):
    app, add, payloads, calls = setup
    add('cheap', ['parcel_identity','assessment'], 1)
    add('fallback', ['parcel_identity','assessment'], 3)
    payloads['cheap.example.gov'] = 503
    run = app.evidence_run(request(max_cost_cents=4))
    assert run['status'] == 'sufficient' and run['calls'] == 2 and run['budgeted_cost_cents'] == 4
    assert run['failures'][0]['sentra_id'] == 'cheap'
    assert app.meta_sentra_state()['summary']['requarantined'] == 1


def test_conflicting_cached_claims_require_review(setup):
    app, add, _, calls = setup
    add('one', ['assessment'], payload=[{'parcel_id':'A-1','assessment':100,'owner':'Example'}])
    add('two', ['assessment'], payload=[{'parcel_id':'A-1','assessment':200,'owner':'Example'}])
    app.evidence_run(request(capabilities=['assessment']))
    # Obtain an independent observation using a changed profile policy and then
    # restore it: snapshots are retained but policies fence cache reuse.
    with app.database.session(write=True) as (c, _):
        row = c.execute('SELECT * FROM evidence_snapshots LIMIT 1').fetchone()
        p = c.execute("SELECT * FROM evidence_profiles WHERE sentra_id='two'").fetchone()
        norm = {'subject':'A-1','matched_records':1,'claims':{'assessment':[{'assessment':200}]}}
        c.execute('INSERT INTO evidence_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (str(uuid4()),row['run_id'],'two',p['activation'],p['version'],'A-1',.8,'https://two.example.gov/data.json','b'*64,'c'*64,json.dumps(norm),'[]',row['retrieved_at'],10))
    calls.clear()
    run = app.evidence_run(request(capabilities=['assessment']))
    assert run['status'] == 'review_required' and run['conflicts'] == ['assessment'] and not calls


def test_changed_profile_or_activation_invalidates_cache(setup):
    app, add, _, _ = setup
    add('bundle', ['parcel_identity','assessment'])
    app.evidence_run(request())
    app.evidence_profile({'sentra_id':'bundle','owner_reviewed':True,'identity_field':'parcel_id',
                         'field_map':{'parcel_identity':['parcel_id'],'assessment':['assessment']}, 'confidence':.9,'note':'Revised policy'})
    assert not app.evidence_plan(request())['cached_snapshot_ids']
    with app.database.session(write=True) as (c, _):
        c.execute("UPDATE activated_sentras SET activated_at='different' WHERE id='bundle'")
    assert not app.evidence_plan(request())['sequence']


@pytest.mark.parametrize('overrides', [{'threshold':float('nan')},{'threshold':True},{'max_calls':True},{'capabilities':['assessment','assessment']},{'subject':''}])
def test_invalid_requests(setup, overrides):
    with pytest.raises(ValueError): setup[0].evidence_plan(request(**overrides))


def test_profile_requires_actual_approval_and_schema_fields(setup):
    app, add, _, _ = setup
    add('one', ['parcel_identity'])
    with pytest.raises(ValueError, match='owner_reviewed'):
        app.evidence_profile({'sentra_id':'one'})
    with pytest.raises(ValueError, match='approved schema'):
        app.evidence_profile({'sentra_id':'one','owner_reviewed':True,'identity_field':'guessed','field_map':{'parcel_identity':['parcel_id']},'note':'Test'})
    assert app.evidence_run(request(jurisdiction='Another County'))['calls'] == 0
