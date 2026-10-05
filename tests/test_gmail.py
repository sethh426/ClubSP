import json
import stat
from urllib.parse import parse_qs, urlsplit

import pytest

from app.gmail import GmailConnection, READ_SCOPE, SEND_SCOPE, SCOPE, load_local_environment

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


def test_authorization_uses_read_and_send_scopes_with_pkce(tmp_path):
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
    assert status["connected"] and status["sending_enabled"] and status["sync_enabled"]
    assert not status["automatic_sync"]
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


def test_configured_https_callback_used_consistently(tmp_path):
    callback = "https://clubsp.online/auth/gmail/callback"
    connection, calls = make_connection(tmp_path)
    connection.external_callback = callback
    url, state = connection.begin(ORIGIN)
    assert parse_qs(urlsplit(url).query)["redirect_uri"] == [callback]
    assert connection.status(ORIGIN)["redirect_uri"] == callback
    connection.complete({"state": state, "code": "synthetic-code"}, state, ORIGIN)
    assert calls[0][1]["data"]["redirect_uri"] == callback
    with pytest.raises(ValueError):
        connection.begin("https://untrusted.example")


@pytest.mark.parametrize("callback", ["http://example.test/auth/gmail/callback",
    "https://user:pass@example.test/auth/gmail/callback",
    "https://example.test/other", "https://example.test/auth/gmail/callback?x=1",
    "https://example.test/auth/gmail/callback#fragment"])
def test_reject_unsafe_external_callback(tmp_path, callback):
    with pytest.raises(ValueError):
        GmailConnection(tmp_path, config=dict(CONFIG, GOOGLE_REDIRECT_URI=callback))


def test_refresh_preserves_refresh_token_and_private_storage(tmp_path):
    connection, calls = make_connection(tmp_path)
    finish(connection)
    assert connection.ensure_access_token() == "synthetic-access"
    assert len(calls) == 2  # Valid tokens do not cause network calls.
    connection.clock = lambda: 3700
    def refresh_request(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/token"):
            assert kwargs["data"]["grant_type"] == "refresh_token"
            assert kwargs["data"]["refresh_token"] == "synthetic-refresh"
            return {"access_token": "new-access", "expires_in": 3600}
        return {"emailAddress": "owner@example.test"}
    connection.request = refresh_request
    assert connection.ensure_access_token() == "new-access"
    saved = json.loads(connection.path.read_text())
    assert saved["refresh_token"] == "synthetic-refresh"
    assert saved["expires_at"] == 7300
    assert stat.S_IMODE(connection.path.stat().st_mode) == 0o600
    assert "new-access" not in json.dumps(connection.status(ORIGIN))


@pytest.mark.parametrize("response", [{}, {"access_token": "a", "expires_in": 0},
    {"access_token": "a", "expires_in": 3600, "scope": "openid"},
    {"access_token": "a", "expires_in": 3600, "refresh_token": ""}])
def test_invalid_refresh_preserves_saved_tokens(tmp_path, response):
    connection, _ = make_connection(tmp_path)
    finish(connection)
    original = connection.path.read_bytes()
    connection.request = lambda *args, **kwargs: response
    with pytest.raises(ValueError):
        connection.ensure_access_token(force=True)
    assert connection.path.read_bytes() == original


def test_disconnect_during_refresh_never_restores_tokens(tmp_path):
    connection, _ = make_connection(tmp_path)
    finish(connection)
    original_request = connection.request
    def request(url, **kwargs):
        result = original_request(url, **kwargs)
        if url.endswith("/profile"):
            connection.disconnect()
        return result
    connection.request = request
    with pytest.raises(ValueError, match="cancelled"):
        connection.ensure_access_token(force=True)
    assert not connection.path.exists()


def test_failed_provider_refresh_preserves_tokens(tmp_path):
    connection, _ = make_connection(tmp_path)
    finish(connection)
    before = connection.path.read_bytes()
    def unavailable(*args, **kwargs):
        raise ValueError("Google connection failed")
    connection.request = unavailable
    with pytest.raises(ValueError):
        connection.ensure_access_token(force=True)
    assert connection.path.read_bytes() == before


def test_refresh_rejects_changed_mailbox(tmp_path):
    connection, _ = make_connection(tmp_path)
    finish(connection)
    before = connection.path.read_bytes()
    request = connection.request
    connection.request = lambda url, **kwargs: ({"emailAddress": "other@example.test"}
        if url.endswith("/profile") else request(url, **kwargs))
    with pytest.raises(ValueError, match="mailbox"):
        connection.ensure_access_token(force=True)
    assert connection.path.read_bytes() == before


def test_refresh_does_not_overwrite_newer_connection(tmp_path):
    connection, _ = make_connection(tmp_path)
    finish(connection)
    request = connection.request
    def replaced(url, **kwargs):
        result = request(url, **kwargs)
        if url.endswith("/profile"):
            saved = json.loads(connection.path.read_text())
            saved["access_token"] = "newer-consent-access"
            connection.path.write_text(json.dumps(saved))
        return result
    connection.request = replaced
    with pytest.raises(ValueError, match="changed"):
        connection.ensure_access_token(force=True)
    assert json.loads(connection.path.read_text())["access_token"] == "newer-consent-access"


def test_refresh_route_checks_origin_and_hides_tokens(tmp_path):
    import threading
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from app.server import create_server
    connection, _ = make_connection(tmp_path / "private")
    finish(connection)
    server = create_server(tmp_path / "app.db", port=0, gmail=connection)
    origin = "http://127.0.0.1:" + str(server.server_address[1])
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        with pytest.raises(HTTPError) as missing:
            urlopen(Request(origin + "/api/gmail/refresh", data=b"{}",
                            headers={"Content-Type": "application/json"}))
        assert missing.value.code == 403
        with urlopen(Request(origin + "/api/gmail/refresh", data=b"{}",
                headers={"Content-Type": "application/json", "Origin": origin})) as response:
            status = json.load(response)
        assert status["connected"] and not status["access_token_expired"]
        assert "synthetic-access" not in json.dumps(status)
        assert "synthetic-refresh" not in json.dumps(status)
    finally:
        server.shutdown(); server.server_close(); worker.join()


def test_send_scope_is_incremental_and_read_only_connection_remains_valid(tmp_path):
    connection, calls = make_connection(tmp_path)
    finish(connection)
    status = connection.status(ORIGIN)
    assert status["connected"] and not status["sending_enabled"]
    with pytest.raises(ValueError, match="Enable approved Gmail sending"):
        connection.ensure_access_token(required_scope=SEND_SCOPE)

    connection.request = lambda url, **kwargs: (
        {"access_token": "send-access", "expires_in": 3600,
         "scope": READ_SCOPE + " " + SEND_SCOPE}
        if url.endswith("/token") else {"emailAddress": "owner@example.test"}
    )
    url, state = connection.begin(ORIGIN, include_send=True)
    query = parse_qs(urlsplit(url).query)
    assert set(query["scope"][0].split()) == {READ_SCOPE, SEND_SCOPE}
    assert query["include_granted_scopes"] == ["true"]
    connection.complete({"state": state, "code": "send-code"}, state, ORIGIN)
    assert connection.status(ORIGIN)["sending_enabled"]
    assert connection.ensure_access_token(required_scope=SEND_SCOPE) == "send-access"
