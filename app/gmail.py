"""Owner-controlled Gmail authorization for manual previews and approved sends."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
# Backward-compatible name used by the read-only authorization tests and inbox code.
SCOPE = READ_SCOPE
CALLBACK = "/auth/gmail/callback"


def scope_set(value):
    return set(value.split()) if isinstance(value, str) else set()


def load_local_environment(path):
    """Read only explicit Google settings; never execute a dotenv file."""
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key in {"GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_MAILBOX_EMAIL", "GOOGLE_REDIRECT_URI", "CLUBSP_OWNER_SECRET", "CLUBSP_ENV"}:
            os.environ.setdefault(key, value.strip())


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def google_request(url, *, data=None, json_body=None, token=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None and json_body is not None:
        raise ValueError("Use one Google request body format")
    body = urlencode(data).encode() if data is not None else json.dumps(json_body).encode() if json_body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif json_body is not None:
        headers["Content-Type"] = "application/json"
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
        self.external_callback = config.get("GOOGLE_REDIRECT_URI", "").strip()
        if self.external_callback:
            parsed = urlsplit(self.external_callback)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.path != CALLBACK or parsed.query or parsed.fragment):
                raise ValueError("GOOGLE_REDIRECT_URI must be an HTTPS Gmail callback URL")
        self.path = Path(directory) / "gmail-token.json"
        self.request = request or google_request
        self.clock = clock or time.time
        self.pending = {}
        self.generation = 0
        self.lock = threading.Lock()
        self.refresh_lock = threading.Lock()
        self.sync_lock = threading.Lock()

    def callback_uri(self, origin):
        return self.external_callback or origin + CALLBACK

    def _saved(self):
        saved = json.loads(self.path.read_text())
        if (not isinstance(saved, dict) or not isinstance(saved.get("email"), str)
                or saved["email"].lower() != self.expected_email
                or saved.get("client_fingerprint") != self.fingerprint()
                or READ_SCOPE not in scope_set(saved.get("scope"))
                or not isinstance(saved.get("expires_at"), (int, float))):
            raise ValueError()
        return saved

    def status(self, origin):
        result = {"configured": bool(self.client_id and self.client_secret and self.expected_email),
                  "connected": False, "sending_enabled": False, "sync_enabled": True,
                  "automatic_sync": False, "sync_mode": "manual_previews",
                  "redirect_uri": self.callback_uri(origin), "scope": "Read-only Gmail",
                  "refresh_enabled": True, "send_scope_available": True,
                  "expected_email": self.expected_email}
        try:
            saved = self._saved()
            granted = scope_set(saved.get("scope"))
            result.update(connected=True, sending_enabled=SEND_SCOPE in granted, email=saved["email"],
                          access_token_expired=self.clock() >= saved["expires_at"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass
        return result

    def fingerprint(self):
        return hashlib.sha256(self.client_id.encode()).hexdigest()

    def begin(self, origin, include_send=False):
        parts = urlsplit(origin)
        if parts.hostname not in {"127.0.0.1", "localhost"} or parts.scheme != "http":
            raise ValueError("This release supports the trusted local application origin only")
        if not self.status(origin)["configured"]:
            raise ValueError("Configure the Google client and expected mailbox before connecting")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        requested = (READ_SCOPE, SEND_SCOPE) if include_send else (READ_SCOPE,)
        with self.lock:
            self.pending = {key: value for key, value in self.pending.items() if value[0] > self.clock()}
            if len(self.pending) >= 32:
                raise ValueError("Too many connection attempts; wait ten minutes")
            self.pending[state] = (
                self.clock() + 600, verifier, self.callback_uri(origin), self.generation, requested
            )
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": self.client_id, "redirect_uri": self.callback_uri(origin),
            "response_type": "code", "scope": " ".join(requested), "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "access_type": "offline", "prompt": "consent",
            "include_granted_scopes": "true", "login_hint": self.expected_email,
        }), state

    def complete(self, params, cookie_state, origin):
        state = params.get("state", "")
        if not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
            raise ValueError("Connection session mismatch. Start again from ClubSP.")
        with self.lock:
            pending = self.pending.pop(state, None)
        if not pending or pending[0] <= self.clock() or pending[2] != self.callback_uri(origin):
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
        granted = scope_set(tokens.get("scope"))
        if not set(pending[4]).issubset(granted):
            raise ValueError("Google did not grant the requested Gmail permission")
        access = tokens.get("access_token")
        refresh = tokens.get("refresh_token")
        if not isinstance(access, str) or not access:
            raise ValueError("Google did not return usable Gmail access")
        if not isinstance(refresh, str) or not refresh:
            try:
                prior = self._saved()
                refresh = prior.get("refresh_token")
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                refresh = None
        if not isinstance(refresh, str) or not refresh:
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
                 "expires_at": self.clock() + lifetime, "client_fingerprint": self.fingerprint(),
                 "scope": " ".join(sorted(granted))}
        self._save(saved, pending[3])
        return self.expected_email

    def _save(self, saved, generation, expected=None):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".gmail-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(saved, stream)
                stream.flush()
                os.fsync(stream.fileno())
            with self.lock:
                if generation != self.generation:
                    raise ValueError("Connection was cancelled. Start again.")
                if expected is not None and (not self.path.exists() or self.path.read_bytes() != expected):
                    raise ValueError("Connection changed during refresh. Try again.")
                os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def ensure_access_token(self, force=False, required_scope=READ_SCOPE):
        """Refresh on demand; never return credentials through a public route."""
        with self.refresh_lock:
            with self.lock:
                try:
                    original = self.path.read_bytes()
                    saved = json.loads(original)
                    if (not isinstance(saved, dict) or saved.get("email") != self.expected_email
                            or saved.get("client_fingerprint") != self.fingerprint()
                            or required_scope not in scope_set(saved.get("scope"))
                            or not all(isinstance(saved.get(key), str) and saved[key]
                                       for key in ("access_token", "refresh_token"))
                            or not isinstance(saved.get("expires_at"), (int, float))):
                        raise ValueError()
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    action = "Enable approved Gmail sending" if required_scope == SEND_SCOPE else "Connect Gmail"
                    raise ValueError(action + " before using this feature") from None
                generation = self.generation
            if not force and saved["expires_at"] > self.clock() + 60:
                return saved["access_token"]
            tokens = self.request("https://oauth2.googleapis.com/token", data={
                "client_id": self.client_id, "client_secret": self.client_secret,
                "refresh_token": saved["refresh_token"], "grant_type": "refresh_token",
            })
            try:
                access = tokens["access_token"]
                lifetime = int(tokens["expires_in"])
                if not isinstance(access, str) or not access or not 0 < lifetime <= 86400:
                    raise ValueError()
                if "scope" in tokens and (
                        not isinstance(tokens["scope"], str)
                        or required_scope not in scope_set(tokens["scope"])):
                    raise ValueError()
                refresh = tokens.get("refresh_token", saved["refresh_token"])
                if not isinstance(refresh, str) or not refresh:
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise ValueError("Google returned invalid refreshed access; reconnect Gmail") from None
            profile = self.request("https://gmail.googleapis.com/gmail/v1/users/me/profile", token=access)
            if str(profile.get("emailAddress", "")).lower() != self.expected_email:
                raise ValueError("The selected mailbox does not match the configured ClubSP mailbox")
            saved.update(access_token=access, refresh_token=refresh, expires_at=self.clock() + lifetime)
            if isinstance(tokens.get("scope"), str):
                saved["scope"] = tokens["scope"]
            self._save(saved, generation, expected=original)
            return access

    def disconnect(self):
        with self.lock:
            self.generation += 1
            self.pending.clear()
            self.path.unlink(missing_ok=True)
        # Local removal only; Google Account permissions are managed separately.
