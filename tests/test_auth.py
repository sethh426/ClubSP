import json
import threading
from http.cookies import SimpleCookie
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler, urlopen

import pytest

from app.auth import OwnerAuth, SESSION_COOKIE, SESSION_TTL
from app.gmail import GmailConnection
from app.server import create_server
from app.service import Application


class StopRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def test_owner_auth_token_tamper_and_expiry():
    auth = OwnerAuth(secret="x" * 24, clock=lambda: 100)
    token = auth.issue()
    assert auth.valid(token)
    assert not auth.valid(token + "x")
    expired = OwnerAuth(secret="x" * 24, clock=lambda: 100 + SESSION_TTL + 1)
    assert not expired.valid(token)


def test_short_owner_secret_rejected():
    with pytest.raises(ValueError, match="24 characters"):
        OwnerAuth(secret="too-short")


def test_local_mode_without_secret_remains_compatible(tmp_path):
    server = create_server(tmp_path / "app.db", port=0, auth=OwnerAuth(secret=""))
    origin = "http://127.0.0.1:" + str(server.server_address[1])
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        with urlopen(origin + "/api/health") as response:
            assert json.load(response)["status"] == "ok"
        with urlopen(origin + "/api/state") as response:
            assert "properties" in json.load(response)
    finally:
        server.shutdown(); server.server_close(); worker.join()


def test_owner_login_blocks_private_api_and_establishes_session(tmp_path):
    auth = OwnerAuth(secret="correct-owner-secret-123456")
    server = create_server(tmp_path / "app.db", port=0, auth=auth)
    origin = "http://127.0.0.1:" + str(server.server_address[1])
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    client = build_opener(StopRedirect())
    try:
        with pytest.raises(HTTPError) as denied:
            client.open(origin + "/api/state")
        assert denied.value.code == 401

        with pytest.raises(HTTPError) as redirect:
            client.open(origin + "/")
        assert redirect.value.code == 303 and redirect.value.headers["Location"] == "/login"

        bad = Request(origin + "/api/auth/login", data=json.dumps({"secret": "wrong"}).encode(),
                      headers={"Content-Type": "application/json", "Origin": origin}, method="POST")
        with pytest.raises(HTTPError) as invalid:
            client.open(bad)
        assert invalid.value.code == 401

        good = Request(origin + "/api/auth/login",
                       data=json.dumps({"secret": "correct-owner-secret-123456"}).encode(),
                       headers={"Content-Type": "application/json", "Origin": origin}, method="POST")
        with client.open(good) as response:
            result = json.load(response)
            cookie = response.headers["Set-Cookie"]
        assert result["authenticated"]
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
        token = SimpleCookie(cookie)[SESSION_COOKIE].value

        request = Request(origin + "/api/state", headers={"Cookie": SESSION_COOKIE + "=" + token})
        with client.open(request) as response:
            assert "properties" in json.load(response)

        logout = Request(origin + "/api/auth/logout", data=b"{}",
                         headers={"Content-Type": "application/json", "Origin": origin,
                                  "Cookie": SESSION_COOKIE + "=" + token}, method="POST")
        with client.open(logout) as response:
            assert json.load(response)["authenticated"] is False
            assert "Max-Age=0" in response.headers["Set-Cookie"]
    finally:
        server.shutdown(); server.server_close(); worker.join()


def test_secure_session_cookie_for_https_private_callback(tmp_path):
    config = {
        "GOOGLE_CLIENT_ID": "synthetic",
        "GOOGLE_CLIENT_SECRET": "synthetic-secret",
        "GOOGLE_MAILBOX_EMAIL": "owner@example.test",
        "GOOGLE_REDIRECT_URI": "https://clubsp.online/auth/gmail/callback",
    }
    gmail = GmailConnection(tmp_path / "private", config=config)
    auth = OwnerAuth(secret="correct-owner-secret-123456")
    app = Application(tmp_path / "app.db")
    server = create_server(app.database.path, port=0, application=app, gmail=gmail, auth=auth)
    origin = "http://127.0.0.1:" + str(server.server_address[1])
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        request = Request(origin + "/api/auth/login",
                          data=json.dumps({"secret": "correct-owner-secret-123456"}).encode(),
                          headers={"Content-Type": "application/json", "Origin": origin}, method="POST")
        with urlopen(request) as response:
            cookie = response.headers["Set-Cookie"]
        assert "Secure" in cookie
    finally:
        server.shutdown(); server.server_close(); worker.join()
