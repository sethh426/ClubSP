import app.meta_sentry_service as meta
import app.shadow_intelligence as shadow
from app.service import Application
from tests.sentra_fixture import seed_sentras


def test_browser_sentra_bootstrap_survives_server_restart(tmp_path,monkeypatch):
    # Record the original module functions so this fixture cannot leak into other tests.
    monkeypatch.setattr(meta,'fetch_source',meta.fetch_source)
    monkeypatch.setattr(shadow,'fetch_source',shadow.fetch_source)
    app=Application(tmp_path/'browser.sqlite3')
    seed_sentras(app)
    state=app.meta_sentra_state()
    restarted=Application(app.database.path)
    seed_sentras(restarted)
    assert restarted.meta_sentra_state()['summary']==state['summary']
    assert len(restarted.evidence_state()['profiles'])==1
    assert restarted._active_source('browser_baseline')['state']=='active'
