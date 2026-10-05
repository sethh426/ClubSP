"""Owner-controlled Gmail authorization for manual previews and approved sends."""
from __future__ import annotations

import base64
import hashlib
import json
from email.message import EmailMessage
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

READ_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
SCOPE = READ_SCOPE


def scope_set(value):
    return set(value.split()) if isinstance(value, str) else set()
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
SCOPES = (READ_SCOPE, SEND_SCOPE)
SCOPE = " ".join(SCOPES)


def has_required_scopes(value):
    return isinstance(value, str) and set(SCOPES).issubset(set(value.split()))
CALLBACK = "/auth/gmail/callback"


def load_local_environment(path):
    """Read only explicit Google settings; never execute a dotenv file."""
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key in {"GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_MAILBOX_EMAIL", "GOOGLE_REDIRECT_URI"}:
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

    def status(self, origin):
        result = {"configured": bool(self.client_id and self.client_secret and self.expected_email),
                  "connected": False, "sending_enabled": False, "sync_enabled": True,
                  "automatic_sync": False, "sync_mode": "manual_previews",
                  "redirect_uri": self.callback_uri(origin), "scope": "Read and approved-send Gmail",
                  "refresh_enabled": True,
                  "expected_email": self.expected_email}
        try:
            saved = json.loads(self.path.read_text())
            if isinstance(saved, dict) and isinstance(saved.get("email"), str) and saved["email"].lower() == self.expected_email and saved.get("client_fingerprint") == self.fingerprint():
                if saved.get("scope") == SCOPE:
                    result.update(connected=True, sending_enabled=True, email=saved["email"],
                                  access_token_expired=self.clock() >= saved["expires_at"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return result

    def fingerprint(self):
        return hashlib.sha256(self.client_id.encode()).hexdigest()

    def begin(self, origin, include_send=False):
        if urlsplit(origin).hostname not in {"127.0.0.1", "localhost"} or urlsplit(origin).scheme != "http":
            raise ValueError("This release supports a local connection only")
        if not self.status(origin)["configured"]:
            raise ValueError("Configure the Google client and expected mailbox before connecting")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if v[0] > self.clock()}
            if len(self.pending) >= 32:
                raise ValueError("Too many connection attempts; wait ten minutes")
            requested = (READ_SCOPE, SEND_SCOPE) if include_send else (READ_SCOPE,)
            self.pending[state] = (self.clock() + 600, verifier, self.callback_uri(origin), self.generation, requested)
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": self.client_id, "redirect_uri": self.callback_uri(origin),
            "response_type": "code", "scope": " ".join(requested), "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
            "login_hint": self.expected_email,
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
        if not has_required_scopes(tokens.get("scope")):
            raise ValueError("Google did not grant the required read and send permissions")
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
                            or not all(isinstance(saved.get(k), str) and saved[k]
                                       for k in ("access_token", "refresh_token"))
                            or not isinstance(saved.get("expires_at"), (int, float))):
                        raise ValueError()
                except (OSError, ValueError, TypeError):
                    raise ValueError("Connect Gmail before refreshing access") from None
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
                if "scope" in tokens and not has_required_scopes(tokens["scope"]):
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

    def send_message(self, recipient, subject, body, request_id):
        """Send one owner-approved plain-text message. No automatic retry."""
        if not isinstance(recipient, str) or not recipient or any(ch in recipient for ch in "\r\n<>"):
            raise ValueError("Recipient email is invalid")
        if not isinstance(subject, str) or not subject or "\r" in subject or "\n" in subject or len(subject) > 200:
            raise ValueError("Subject is invalid")
        if not isinstance(body, str) or not body or len(body) > 8000:
            raise ValueError("Message body is invalid")
        if not isinstance(request_id, str) or len(request_id) > 80:
            raise ValueError("Send request identity is invalid")
        access = self.ensure_access_token(required_scope=SEND_SCOPE)
        message = EmailMessage()
        message["To"] = recipient
        message["From"] = self.expected_email
        message["Subject"] = subject
        message["X-ClubSP-Request-ID"] = request_id
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
        result = self.request("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                              token=access, json_body={"raw": raw})
        message_id, thread_id = result.get("id"), result.get("threadId")
        if (not isinstance(message_id, str) or not message_id or len(message_id) > 200
                or not isinstance(thread_id, str) or not thread_id or len(thread_id) > 200):
            raise ValueError("Google returned an invalid send receipt")
        return {"message_id": message_id, "thread_id": thread_id}

    def disconnect(self):
        with self.lock:
            self.generation += 1
            self.pending.clear()
            self.path.unlink(missing_ok=True)
        # Local removal only; Google Account permissions are managed separately.
