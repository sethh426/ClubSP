import json
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

from app.auth import OwnerAuth
from app.gmail import GmailConnection, load_local_environment
from app.preflight import database_ready, deployment_readiness
from app.server import create_server
from app.service import Application


def gmail(tmp_path, callback=""):
    return GmailConnection(tmp_path / "private", config={
        "GOOGLE_CLIENT_ID": "client",
        "GOOGLE_CLIENT_SECRET": "secret",
        "GOOGLE_MAILBOX_EMAIL": "owner@example.test",
        "GOOGLE_REDIRECT_URI": callback,
    })


def test_database_readiness_detects_missing_and_valid_workspace(tmp_path):
    ok, reason = database_ready(tmp_path / "missing.db")
    assert not ok and reason == "database_missing"
    app = Application(tmp_path / "app.db")
    ok, reason = database_ready(app.database.path)
    assert ok and reason == "ok"


def test_production_requires_auth_and_https_callback(tmp_path):
    app = Application(tmp_path / "app.db")
    result = deployment_readiness(app.database.path, gmail=gmail(tmp_path),
                                  auth=OwnerAuth(secret=""), environment="production")
    assert not result["ready"]
    assert result["checks"]["owner_auth"] == "required"
    assert result["checks"]["https_callback"] == "required"

    result = deployment_readiness(
        app.database.path,
        gmail=gmail(tmp_path, "https://clubsp.online/auth/gmail/callback"),
        auth=OwnerAuth(secret="correct-owner-secret-123456"),
        environment="production",
    )
    assert result["ready"]
    assert result["checks"]["database"] == "ok"
    assert result["checks"]["owner_auth"] == "enabled"
    assert result["checks"]["https_callback"] == "configured"


def test_invalid_environment_is_not_ready(tmp_path):
    app = Application(tmp_path / "app.db")
    result = deployment_readiness(app.database.path, gmail=gmail(tmp_path),
                                  auth=OwnerAuth(secret=""), environment="staging-ish")
    assert not result["ready"] and result["checks"]["environment"] == "invalid"


def test_ready_endpoint_is_public_but_private_state_stays_authenticated(tmp_path):
    app = Application(tmp_path / "app.db")
    g = gmail(tmp_path, "https://clubsp.online/auth/gmail/callback")
    auth = OwnerAuth(secret="correct-owner-secret-123456")
    server = create_server(app.database.path, port=0, application=app, gmail=g, auth=auth)
    origin = "http://127.0.0.1:" + str(server.server_address[1])
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        with urlopen(origin + "/api/ready") as response:
            body = json.load(response)
        assert body["ready"]
        assert "secret" not in json.dumps(body).lower()
        try:
            urlopen(origin + "/api/state")
            raise AssertionError("private state unexpectedly accessible")
        except HTTPError as error:
            assert error.code == 401
    finally:
        server.shutdown(); server.server_close(); worker.join()


def test_env_loader_accepts_clubsp_private_settings(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "CLUBSP_ENV=production\n"
        "CLUBSP_OWNER_SECRET=correct-owner-secret-123456\n"
        "GOOGLE_REDIRECT_URI=https://clubsp.online/auth/gmail/callback\n"
        "IGNORED_KEY=must-not-load\n"
    )
    for key in ("CLUBSP_ENV", "CLUBSP_OWNER_SECRET", "GOOGLE_REDIRECT_URI", "IGNORED_KEY"):
        monkeypatch.delenv(key, raising=False)
    load_local_environment(path)
    import os
    assert os.environ["CLUBSP_ENV"] == "production"
    assert os.environ["CLUBSP_OWNER_SECRET"] == "correct-owner-secret-123456"
    assert os.environ["GOOGLE_REDIRECT_URI"] == "https://clubsp.online/auth/gmail/callback"
    assert "IGNORED_KEY" not in os.environ
