"""Single-owner session authentication for private ClubSP deployments."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time


SESSION_COOKIE = "clubsp_session"
SESSION_TTL = 12 * 60 * 60


def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class OwnerAuth:
    def __init__(self, secret=None, clock=None):
        self.secret = (os.environ.get("CLUBSP_OWNER_SECRET", "") if secret is None else secret)
        self.clock = clock or time.time
        if self.secret and len(self.secret) < 24:
            raise ValueError("CLUBSP_OWNER_SECRET must be at least 24 characters")
        self.key = hashlib.sha256(("clubsp-session:" + self.secret).encode()).digest() if self.secret else b""

    @property
    def enabled(self):
        return bool(self.secret)

    def issue(self):
        if not self.enabled:
            raise ValueError("Owner authentication is not configured")
        payload = {"iat": int(self.clock()), "exp": int(self.clock()) + SESSION_TTL, "nonce": secrets.token_hex(8)}
        encoded = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        signature = _b64(hmac.new(self.key, encoded.encode(), hashlib.sha256).digest())
        return encoded + "." + signature

    def valid(self, token):
        if not self.enabled:
            return True
        if not isinstance(token, str) or token.count(".") != 1:
            return False
        encoded, signature = token.split(".", 1)
        expected = _b64(hmac.new(self.key, encoded.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return False
        try:
            payload = json.loads(_unb64(encoded))
            now = int(self.clock())
            return (isinstance(payload, dict)
                    and isinstance(payload.get("iat"), int)
                    and isinstance(payload.get("exp"), int)
                    and payload["iat"] <= now <= payload["exp"]
                    and payload["exp"] - payload["iat"] == SESSION_TTL)
        except (ValueError, TypeError, json.JSONDecodeError):
            return False

    def verify_secret(self, candidate):
        return bool(self.enabled and isinstance(candidate, str)
                    and hmac.compare_digest(candidate.encode(), self.secret.encode()))

    @staticmethod
    def cookie(token, secure=False, max_age=SESSION_TTL):
        secure_flag = "; Secure" if secure else ""
        return (SESSION_COOKIE + "=" + token + "; Path=/; HttpOnly; SameSite=Strict; Max-Age="
                + str(max_age) + secure_flag)

    @staticmethod
    def clear_cookie(secure=False):
        return OwnerAuth.cookie("", secure=secure, max_age=0)
