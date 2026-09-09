"""Single shared password protecting the whole app.

ESP is deployed on a public URL and holds prospect data - IPs, CRM UIDs and
campaign attribution - so every route except the login endpoint and the static
shell requires a session cookie.  The cookie is an HMAC of its own expiry, so
there is no server-side session store to keep in sync across workers.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

from .config import BASE_DIR, DATA_DIR

COOKIE_NAME = "esp_session"
SESSION_TTL = int(os.environ.get("ESP_SESSION_HOURS", "12")) * 3600
_SECRET_FILE = DATA_DIR / ".session_secret"


def password() -> str:
    return os.environ.get("ESP_PASSWORD", "")


def enabled() -> bool:
    """Auth is on whenever a password is configured."""
    return bool(password())


def _secret() -> bytes:
    """Server-side signing key, persisted so restarts do not log everyone out."""
    env = os.environ.get("ESP_SECRET", "")
    if env:
        return hashlib.sha256(env.encode()).digest()
    try:
        return bytes.fromhex(_SECRET_FILE.read_text().strip())
    except (OSError, ValueError):
        key = secrets.token_bytes(32)
        try:
            _SECRET_FILE.write_text(key.hex())
            os.chmod(_SECRET_FILE, 0o600)
        except OSError:
            pass
        return key


def check_password(candidate: str) -> bool:
    expected = password()
    if not expected:
        return True
    return hmac.compare_digest((candidate or "").encode(), expected.encode())


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(ttl: int = SESSION_TTL) -> str:
    exp = str(int(time.time()) + ttl)
    return f"{exp}.{_sign(exp)}"


def valid_token(token: Optional[str]) -> bool:
    if not enabled():
        return True
    if not token or "." not in token:
        return False
    exp, _, sig = token.partition(".")
    if not hmac.compare_digest(sig, _sign(exp)):
        return False
    try:
        return int(exp) > time.time()
    except ValueError:
        return False
