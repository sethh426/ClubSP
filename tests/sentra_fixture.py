"""Synthetic public Sentras for isolated browser validation only."""
import httpx
import app.meta_sentry_service as meta
import app.shadow_intelligence as shadow
from app.meta_sentras import SourceCandidate
from app.meta_source_transport import fetch_source


def seed_sentras(app):
    transport=httpx.MockTransport(lambda request:httpx.Response(200,json=[{'parcel_id':'synthetic-A1','assessment':123000}]))
    original=meta.fetch_source
    def acquire(url,**kwargs):
        if url.startswith('https://synthetic-sentra.example.gov/'):
            return fetch_source(url,transport=transport,**kwargs)
        return original(url,**kwargs)
    meta.fetch_source=acquire
    shadow.fetch_source=acquire
    sources=[]
    for name in ('baseline','challenger'):
        candidate=SourceCandidate('data_gov','browser-'+name,'Synthetic browser '+name,
            'https://synthetic-sentra.example.gov/'+name+'.json',jurisdiction_hint='Browser Fixture County',
            capabilities_hint=('parcel_identity','assessment'))
        app._store_discovered_candidates('data_gov','synthetic fixture',[candidate],'a'*64)
        app.meta_probe({'fingerprint':candidate.fingerprint})
        sources.append(candidate)
    app.meta_propose({'fingerprint':sources[0].fingerprint})
    app.meta_review({'fingerprint':sources[0].fingerprint,'decision':'approve','note':'Synthetic test only'})
    app.meta_activate({'fingerprint':sources[0].fingerprint,'sentra_id':'browser_baseline','family':'parcel_assessor',
        'acquisition_mode':'direct_http','jurisdiction':'Browser Fixture County','rights_note':'Isolated browser test'})
    app.evidence_profile({'sentra_id':'browser_baseline','owner_reviewed':True,'identity_field':'parcel_id',
        'field_map':{'parcel_identity':['parcel_id'],'assessment':['assessment']},'confidence':.8,'note':'Synthetic mapping'})
