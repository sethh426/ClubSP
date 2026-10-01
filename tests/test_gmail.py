import json
import stat
from urllib.parse import parse_qs, urlsplit

import pytest

from app.gmail import GmailConnection, SCOPE, load_local_environment

ORIGIN = "http://127.0.0.1:8000"
CONFIG = {"GOOGLE_CLIENT_ID": "synthetic-client", "GOOGLE_CLIENT_SECRET": "synthetic-secret",
          "GOOGLE_MAILBOX_EMAIL": "owner@example.test"}


def make_connection(tmp_path, scope=SCOPE, email="owner@example.test", refresh="synthetic-refresh"):
    calls = []
    def request(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/token"):
            return {"access_token": "synthetic-access", "refresh_token": refresh,
                    "expires_in": 3600, "scope": scope}
        return {"emailAddress": email}
    return GmailConnection(tmp_path, config=CONFIG, request=request, clock=lambda: 100), calls


def finish(connection):
    _, state = connection.begin(ORIGIN)
    return connection.complete({"state": state, "code": "synthetic-code"}, state, ORIGIN)


def test_authorization_is_read_only_pkce_and_fixed_destination(tmp_path):
    connection, _ = make_connection(tmp_path)
    url, state = connection.begin(ORIGIN)
    query = parse_qs(urlsplit(url).query)
    assert urlsplit(url).netloc == "accounts.google.com"
    assert query["scope"] == [SCOPE]
    assert query["state"] == [state]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [ORIGIN + "/auth/gmail/callback"]
    assert "synthetic-secret" not in url


def test_token_private_persistent_and_never_returned(tmp_path):
    connection, calls = make_connection(tmp_path)
    assert finish(connection) == "owner@example.test"
    assert stat.S_IMODE(connection.path.stat().st_mode) == 0o600
    status = connection.status(ORIGIN)
    assert status["connected"] and not status["sending_enabled"] and not status["sync_enabled"]
    assert "synthetic-access" not in json.dumps(status)
    assert "synthetic-refresh" not in json.dumps(status)
    assert len(calls) == 2
    assert "code_verifier" in calls[0][1]["data"]
    restarted, _ = make_connection(tmp_path)
    assert restarted.status(ORIGIN)["connected"]
    restarted.disconnect()
    assert not restarted.status(ORIGIN)["connected"]


@pytest.mark.parametrize("scope,email,refresh", [("openid", "owner@example.test", "r"),
    (SCOPE, "other@example.test", "r"), (SCOPE, "owner@example.test", "")])
def test_scope_mailbox_and_refresh_required(tmp_path, scope, email, refresh):
    connection, _ = make_connection(tmp_path, scope, email, refresh)
    with pytest.raises(ValueError):
        finish(connection)
    assert not connection.path.exists()


def test_state_cookie_replay_expiration_and_wrong_origin(tmp_path):
    connection, calls = make_connection(tmp_path)
    _, state = connection.begin(ORIGIN)
    with pytest.raises(ValueError):
        connection.complete({"state": state, "code": "code"}, "wrong", ORIGIN)
    assert not calls
    connection.complete({"state": state, "code": "code"}, state, ORIGIN)
    with pytest.raises(ValueError):
        connection.complete({"state": state, "code": "code"}, state, ORIGIN)
    _, state = connection.begin(ORIGIN)
    connection.clock = lambda: 1000
    with pytest.raises(ValueError):
        connection.complete({"state": state, "code": "code"}, state, ORIGIN)
    with pytest.raises(ValueError):
        connection.begin("https://untrusted.example")


def test_denial_does_not_replace_previous_connection(tmp_path):
    connection, calls = make_connection(tmp_path)
    finish(connection)
    before = connection.path.read_bytes()
    _, state = connection.begin(ORIGIN)
    with pytest.raises(ValueError):
        connection.complete({"state": state, "error": "access_denied"}, state, ORIGIN)
    assert before == connection.path.read_bytes()
    assert len(calls) == 2


def test_missing_settings_and_new_client_do_not_claim_connected(tmp_path):
    connection = GmailConnection(tmp_path, config={})
    with pytest.raises(ValueError):
        connection.begin(ORIGIN)
    saved, _ = make_connection(tmp_path)
    finish(saved)
    config = dict(CONFIG, GOOGLE_CLIENT_ID="different-client")
    assert not GmailConnection(tmp_path, config=config).status(ORIGIN)["connected"]


def test_dotenv_does_not_execute_or_override(tmp_path, monkeypatch):
    path = tmp_path / "env"
    path.write_text("GOOGLE_CLIENT_ID=synthetic\nIGNORED=bad\nGOOGLE_CLIENT_SECRET=$(not-executed)\n")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "existing")
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    load_local_environment(path)
    import os
    assert os.environ["GOOGLE_CLIENT_ID"] == "existing"
    assert os.environ["GOOGLE_CLIENT_SECRET"] == "$(not-executed)"


def test_http_connection_cookie_callback_and_secret_free_logs(tmp_path, capsys):
    import threading
    from urllib.request import Request, build_opener, HTTPRedirectHandler
    from urllib.error import HTTPError
    from app.server import create_server
    connection, calls = make_connection(tmp_path / "private")
    server = create_server(tmp_path / "app.db", port=0, gmail=connection)
    origin = "http://127.0.0.1:" + str(server.server_address[1])
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    class StopRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None
    client = build_opener(StopRedirect())
    try:
        body = b"{}"
        with pytest.raises(HTTPError) as missing:
            client.open(Request(origin + "/api/gmail/connect", data=body, headers={"Content-Type": "application/json"}))
        assert missing.value.code == 403
        with client.open(Request(origin + "/api/gmail/connect", data=body,
                headers={"Content-Type": "application/json", "Origin": origin})) as response:
            result = json.load(response)
            cookie = response.headers["Set-Cookie"].split(";")[0]
            assert "HttpOnly" in response.headers["Set-Cookie"]
        state = parse_qs(urlsplit(result["authorization_url"]).query)["state"][0]
        with pytest.raises(HTTPError) as redirect:
            client.open(Request(origin + "/auth/gmail/callback?state=" + state + "&code=synthetic-private-code",
                                headers={"Cookie": cookie}))
        assert redirect.value.code == 303
        assert redirect.value.headers["Location"] == "/?gmail=connected"
        with client.open(origin + "/api/gmail/status") as response:
            status = json.load(response)
        assert status["connected"]
        assert "synthetic-access" not in json.dumps(status)
        assert len(calls) == 2
    finally:
        server.shutdown(); server.server_close(); worker.join()
    log = capsys.readouterr().err
    assert "synthetic-private-code" not in log and state not in log


def test_disconnect_cancels_inflight_connection(tmp_path):
    connection, _ = make_connection(tmp_path)
    original = connection.request
    def cancel_request(url, **kwargs):
        result = original(url, **kwargs)
        if url.endswith("/profile"):
            connection.disconnect()
        return result
    connection.request = cancel_request
    with pytest.raises(ValueError, match="cancelled"):
        finish(connection)
    assert not connection.path.exists()
