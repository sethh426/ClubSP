import json
from uuid import uuid4
import httpx
import pytest
import app.meta_sentry_service as meta
import app.property_assistant as assistant
from app.meta_source_transport import fetch_source
from app.meta_sentras import SourceCandidate
from app.service import Application
from app.property_assistant import SOURCE,ADDRESS,IDENTITY,VALUE

@pytest.fixture
def county(tmp_path,monkeypatch):
    a=Application(tmp_path/'check.sqlite3');queries=[]
    fields=[{'name':f,'type':t} for f,t in [('OBJECTID','esriFieldTypeOID'),(ADDRESS,'esriFieldTypeString'),(IDENTITY,'esriFieldTypeString'),(VALUE,'esriFieldTypeInteger')]]
    def handle(r):
        if not r.url.path.endswith('/query'):
            return httpx.Response(200,json={'fields':fields,'advancedQueryCapabilities':{'supportsPagination':False}})
        queries.append(dict(r.url.params))
        if r.url.params.get('returnIdsOnly'):
            return httpx.Response(200,json={'objectIdFieldName':'OBJECTID','objectIds':[200]})
        return httpx.Response(200,json={'features':[{'attributes':{'OBJECTID':200,ADDRESS:'123 MAIN ST',IDENTITY:'P-1',VALUE:150000}}]})
    transport=httpx.MockTransport(handle)
    fetch=lambda url,**kw:fetch_source(url,transport=transport,**kw)
    monkeypatch.setattr(meta,'fetch_source',fetch);monkeypatch.setattr(assistant,'fetch_source',fetch)
    c=SourceCandidate('arcgis_hub','county','County parcel assessor sales','https://example.gov/MapServer/0',description='parcel assessor property sale GIS',jurisdiction_hint='Allen County, Indiana',capabilities_hint=('parcel_identity','assessment'))
    a._store_discovered_candidates('arcgis_online','county',[c],'a'*64)
    a.meta_probe({'fingerprint':c.fingerprint});a.meta_propose({'fingerprint':c.fingerprint})
    a.meta_review({'fingerprint':c.fingerprint,'decision':'approve','note':'Synthetic approved county fixture'})
    a.meta_activate({'fingerprint':c.fingerprint,'sentra_id':SOURCE,'family':'assessor','acquisition_mode':'arcgis','jurisdiction':'Allen County, Indiana','rights_note':'Synthetic only'})
    a.evidence_profile({'sentra_id':SOURCE,'owner_reviewed':True,'identity_field':IDENTITY,'field_map':{'parcel_identity':[IDENTITY],'assessment':[VALUE]},'confidence':.7,'note':'Synthetic reviewed mapping'})
    return a,queries

def test_address_research_hides_policy_and_targets_property(county):
    a,queries=county
    found=a.property_search({'address':'123 Main'})
    assert found['matches']==[{'subject':'P-1','address':'123 MAIN ST'}]
    result=a.property_research({'subject':'P-1','request_key':str(uuid4()),'watch':True})
    assert result['status']=='sufficient' and result['assessed_value']==150000
    assert result['watching'] and not result['facts_imported']
    assert any(q['where']==IDENTITY+" = 'P-1'" for q in queries)
    assert a.temporal_state()['events']==[] # targeted rows do not distort whole-source timing
    again=a.property_research({'subject':'P-1','request_key':str(uuid4()),'watch':True})
    assert again['status']=='sufficient' and len(a.property_watches()['watches'])==1

@pytest.mark.parametrize('address',['Main','123 Main%','123 Main; DELETE','123 Main_'])
def test_address_input_is_bounded_and_not_sql(county,address):
    with pytest.raises(ValueError):county[0].property_search({'address':address})

def test_daily_watch_restart_due_claim_stop(county):
    a,_=county
    a.property_research({'subject':'P-1','request_key':str(uuid4()),'watch':True})
    restarted=Application(a.database.path)
    assert restarted.property_watch_cycle()=={'checked':0}
    with a.database.session(write=True) as (c,_):c.execute('UPDATE property_watches SET next_due=0')
    assert restarted.property_watch_cycle()=={'checked':1}
    assert a.property_watch_cycle()=={'checked':0}
    watch=a.property_watches()['watches'][0]
    a.property_watch_stop({'id':watch['id']})
    assert not a.property_watches()['watches'][0]['enabled']

def test_watch_unavailable_source_never_reactivates(county,monkeypatch):
    a,_=county;a.property_research({'subject':'P-1','request_key':str(uuid4()),'watch':True})
    with a.database.session(write=True) as (c,_):c.execute('UPDATE property_watches SET next_due=0')
    monkeypatch.setattr(a,'_property_source',lambda:(_ for _ in ()).throw(LookupError('inactive')))
    assert a.property_watch_cycle()=={'checked':1}
    assert a.property_watches()['watches'][0]['result']['status']=='unavailable'
