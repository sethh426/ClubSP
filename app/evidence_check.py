"""Live Spike smoke check in a disposable database; no production activation."""
import json
from pathlib import Path
import tempfile
from uuid import uuid4

from .evidence_compiler import records
from .meta_sentras import SourceCandidate
from .service import Application

URL = 'https://services5.arcgis.com/Fq0TUmdRNerGcci4/arcgis/rest/services/Fritz_Rd_Parcels_view_layer/FeatureServer/0'


def check():
    with tempfile.TemporaryDirectory(prefix='clubsp-evidence-check-') as directory:
        app = Application(Path(directory) / 'check.sqlite3')
        candidate = SourceCandidate('arcgis_hub', 'live-evidence-smoke', 'Public parcel smoke fixture', URL,
                                    jurisdiction_hint='Smoke test only', capabilities_hint=('parcel_identity',))
        app._store_discovered_candidates('arcgis_online', 'smoke', [candidate], 'a'*64)
        app.meta_probe({'fingerprint': candidate.fingerprint})
        app.meta_propose({'fingerprint': candidate.fingerprint})
        app.meta_review({'fingerprint': candidate.fingerprint, 'decision':'approve',
                         'note':'Disposable connectivity test; does not certify accuracy or grant production approval'})
        app.meta_activate({'fingerprint':candidate.fingerprint,'sentra_id':'live_check','family':'parcel_assessor',
                           'acquisition_mode':'arcgis','jurisdiction':'Smoke test only','rights_note':'Public endpoint, disposable test only'})
        result = app.meta_execute({'sentra_id':'live_check'})
        row = next(r for r in records(result['payload']) if r.get('PARCEL_ID'))
        app.evidence_profile({'sentra_id':'live_check','owner_reviewed':True,'identity_field':'PARCEL_ID',
                              'field_map':{'parcel_identity':['PARCEL_ID']},'confidence':.5,'note':'Connectivity test weight'})
        request = {'subject':str(row['PARCEL_ID']).strip(),'jurisdiction':'Smoke test only',
                   'capabilities':['parcel_identity'],'threshold':.5,'max_cost_cents':0,'request_key':str(uuid4())}
        run = app.evidence_run(request)
        restarted = Application(app.database.path)
        cached = restarted.evidence_run({**request,'request_key':str(uuid4())})
        assert run['status'] == cached['status'] == 'sufficient'
        assert run['calls'] == 1 and cached['calls'] == 0
        assert not run['facts_imported']
        report = {'status':'passed','live_compiler_calls':run['calls'],'restart_cache_calls':cached['calls'],
                  'snapshot_count':len(run['snapshot_ids']),'production_activation':False,'facts_imported':False}
        return report


if __name__ == '__main__':
    print(json.dumps(check(), indent=2))
