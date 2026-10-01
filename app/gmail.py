"""Owner-only, read-only Gmail authorization. No send or inbox import operations."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path
import secrets
import tempfile
import threading
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CALLBACK = "/auth/gmail/callback"


def configured_https_origin(value):
    """Accept one explicit canonical HTTPS domain; never infer it from headers."""
    if not value:
        return ""
    parts = urlsplit(value)
    host = parts.hostname or ""
    if (parts.scheme != "https" or value != "https://" + host or len(host) > 253
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", host)):
        raise ValueError("CLUBSP_GMAIL_ORIGIN must be an HTTPS domain origin without a port or path")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return value
    raise ValueError("CLUBSP_GMAIL_ORIGIN requires a domain, not an IP address")


def load_local_environment(path):
    """Read only explicit authorization settings; never execute a dotenv file."""
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key in {"GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_MAILBOX_EMAIL", "CLUBSP_GMAIL_ORIGIN"}:
            os.environ.setdefault(key, value.strip())


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def google_request(url, *, data=None, token=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = urlencode(data).encode() if data is not None else None
    if body:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        with build_opener(NoRedirect()).open(Request(url, data=body, headers=headers), timeout=10) as response:
            content = response.read(65537)
            if len(content) > 65536:
                raise ValueError()
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError()
            return result
    except Exception:
        # Provider responses and exception URLs may contain credential material.
        raise ValueError("Google connection failed. Check API access and try connecting again.") from None


class GmailConnection:
    def __init__(self, directory, config=None, request=None, clock=None):
        config = os.environ if config is None else config
        self.client_id = config.get("GOOGLE_CLIENT_ID", "")
        self.client_secret = config.get("GOOGLE_CLIENT_SECRET", "")
        self.expected_email = config.get("GOOGLE_MAILBOX_EMAIL", "").strip().lower()
        self.oauth_origin = configured_https_origin(config.get("CLUBSP_GMAIL_ORIGIN", "").strip())
        self.path = Path(directory) / "gmail-token.json"
        self.request = request or google_request
        self.clock = clock or time.time
        self.pending = {}
        self.generation = 0
        self.lock = threading.Lock()

    def status(self, origin):
        result = {"configured": bool(self.client_id and self.client_secret and self.expected_email),
                  "connected": False, "sending_enabled": False, "sync_enabled": False,
                  "redirect_uri": origin + CALLBACK, "scope": "Read-only Gmail",
                  "expected_email": self.expected_email,
                  "authorization_mode": "private_https" if self.oauth_origin else "local"}
        try:
            saved = json.loads(self.path.read_text())
            if isinstance(saved, dict) and isinstance(saved.get("email"), str) and saved["email"].lower() == self.expected_email and saved.get("client_fingerprint") == self.fingerprint():
                result.update(connected=True, email=saved["email"],
                              access_token_expired=self.clock() >= saved["expires_at"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return result

    def fingerprint(self):
        return hashlib.sha256(self.client_id.encode()).hexdigest()

    def begin(self, origin):
        parts = urlsplit(origin)
        if self.oauth_origin:
            trusted = origin == self.oauth_origin
        else:
            trusted = (parts.hostname in {"127.0.0.1", "localhost"}
                       and parts.scheme == "http" and not parts.username
                       and not parts.password and not parts.path
                       and not parts.query and not parts.fragment)
        if not trusted:
            raise ValueError("Use the configured Gmail authorization origin")
        if not self.status(origin)["configured"]:
            raise ValueError("Configure the Google client and expected mailbox before connecting")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if v[0] > self.clock()}
            if len(self.pending) >= 32:
                raise ValueError("Too many connection attempts; wait ten minutes")
            self.pending[state] = (self.clock() + 600, verifier, origin + CALLBACK, self.generation)
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": self.client_id, "redirect_uri": origin + CALLBACK,
            "response_type": "code", "scope": SCOPE, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "access_type": "offline", "prompt": "consent", "login_hint": self.expected_email,
        }), state

    def complete(self, params, cookie_state, origin):
        state = params.get("state", "")
        if not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
            raise ValueError("Connection session mismatch. Start again from ClubSP.")
        with self.lock:
            pending = self.pending.pop(state, None)
        if not pending or pending[0] <= self.clock() or pending[2] != origin + CALLBACK:
            raise ValueError("Connection session expired or already used. Start again.")
        if params.get("error"):
            raise ValueError("Gmail permission was not granted. No connection was saved.")
        code = params.get("code", "")
        if not code or len(code) > 4096:
            raise ValueError("Missing or invalid Google authorization code")
        tokens = self.request("https://oauth2.googleapis.com/token", data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "code": code, "code_verifier": pending[1], "redirect_uri": pending[2],
            "grant_type": "authorization_code",
        })
        if not isinstance(tokens.get("scope"), str) or SCOPE not in tokens["scope"].split():
            raise ValueError("Google did not grant the required read-only permission")
        access = tokens.get("access_token")
        refresh = tokens.get("refresh_token")
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            raise ValueError("Google did not return offline access. Connect again and approve consent.")
        try:
            lifetime = int(tokens["expires_in"])
            if not 0 < lifetime <= 86400:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise ValueError("Google returned an invalid token expiry") from None
        profile = self.request("https://gmail.googleapis.com/gmail/v1/users/me/profile", token=access)
        if str(profile.get("emailAddress", "")).lower() != self.expected_email:
            raise ValueError("The selected mailbox does not match the configured ClubSP mailbox")
        saved = {"email": self.expected_email, "access_token": access, "refresh_token": refresh,
                 "expires_at": self.clock() + lifetime, "client_fingerprint": self.fingerprint(), "scope": SCOPE}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".gmail-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(saved, stream)
                stream.flush()
                os.fsync(stream.fileno())
            with self.lock:
                if pending[3] != self.generation:
                    raise ValueError("Connection was cancelled. Start again.")
                os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return self.expected_email

    def disconnect(self):
        with self.lock:
            self.generation += 1
            self.pending.clear()
            self.path.unlink(missing_ok=True)
        # Local removal only; Google Account permissions are managed separately.
