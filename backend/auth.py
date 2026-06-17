"""
Authentication for spud-router.

Uses a simple in-memory token store. Tokens are issued on login and expire
after TOKEN_TTL seconds. The token must be presented in either:
  - X-Session-Token header
  - spud_token cookie

Credentials are stored in /etc/spud-router/auth.json as a scrypt hash.
Environment variables SPUD_USER and SPUD_PASS override the file.

Password hash format: "scrypt:<base64(salt+dk)>" where salt is 16 bytes and
dk is derived with N=2**14, r=8, p=1 (fits in ~100 ms on low-end ARM).

Older installations stored a bare SHA-256 hex digest under the key
"password_sha256". On first successful login with the old format the hash
is transparently upgraded to scrypt and saved back to auth.json.

There is also a CLI service token stored at /etc/spud-router/cli-token,
issued at install time, which allows the spud-cli to authenticate without
prompting the user on every SSH session.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import HTTPException, Request

from .state import AUTH_FILE, SPUD_CONF

TOKEN_TTL      = 8 * 3600   # 8 hours
CLI_TOKEN_FILE = SPUD_CONF / "cli-token"

# scrypt parameters — tuned for ~100 ms on a 1 GHz ARM Cortex-A53
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_LEN = 16

# In-memory token store: {token: expiry_unix_timestamp}
_tokens: dict[str, float] = {}


# ── Password hashing ──────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    """Return a scrypt hash string suitable for storage."""
    salt = os.urandom(_SALT_LEN)
    dk   = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return "scrypt:" + base64.b64encode(salt + dk).decode()


def _verify_password_hash(password: str, stored: str) -> bool:
    """
    Return True if password matches stored hash.

    Accepts both the new scrypt format ("scrypt:<b64>") and the legacy
    bare SHA-256 hex format used by older installations.
    """
    if stored.startswith("scrypt:"):
        raw  = base64.b64decode(stored[len("scrypt:"):])
        salt = raw[:_SALT_LEN]
        dk   = raw[_SALT_LEN:]
        attempt = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
        return hmac.compare_digest(attempt, dk)
    # Legacy: bare SHA-256 hex (64 chars)
    attempt_sha256 = hashlib.sha256(password.encode()).hexdigest()
    return hmac.compare_digest(attempt_sha256, stored)


def _is_legacy_hash(stored: str) -> bool:
    """Return True if stored hash uses the old SHA-256 format."""
    return not stored.startswith("scrypt:") and len(stored) == 64


# ── Credential storage ────────────────────────────────────────────────────────

def _load_credentials() -> tuple[str, str]:
    """
    Return (username, password_hash).

    Env vars take priority. The returned hash may be in legacy SHA-256 or
    current scrypt format — callers should use _verify_password_hash().
    """
    env_user = os.environ.get("SPUD_USER")
    env_pass = os.environ.get("SPUD_PASS")
    if env_user and env_pass:
        # Env-var credentials are always re-hashed on the fly (not persisted)
        return env_user, _hash_password(env_pass)

    if AUTH_FILE.exists():
        data = json.loads(AUTH_FILE.read_text())
        # Support both new "password_hash" key and legacy "password_sha256"
        stored = data.get("password_hash") or data.get("password_sha256", "")
        return data["username"], stored

    # Default credentials — insecure, replaced by installer
    return "admin", hashlib.sha256(b"spudrouter").hexdigest()


def verify_credentials(username: str, password: str) -> bool:
    """
    Return True if the supplied credentials are correct.

    On success with a legacy SHA-256 hash the credential is transparently
    re-saved using scrypt so the upgrade happens without user action.
    """
    stored_user, stored_hash = _load_credentials()
    if not secrets.compare_digest(username, stored_user):
        return False
    if not _verify_password_hash(password, stored_hash):
        return False
    # Transparently upgrade legacy SHA-256 hash to scrypt on first login
    if _is_legacy_hash(stored_hash) and AUTH_FILE.exists():
        _save_hash(stored_user, _hash_password(password))
    return True


def _save_hash(username: str, password_hash: str) -> None:
    """Write username + hash to auth.json."""
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps({"username": username, "password_hash": password_hash}))
    AUTH_FILE.chmod(0o600)


def update_password(new_password: str) -> None:
    """Persist a new scrypt password hash to auth.json."""
    stored_user, _ = _load_credentials()
    _save_hash(stored_user, _hash_password(new_password))


# ── Token management ──────────────────────────────────────────────────────────

def create_token() -> str:
    """Issue a new session token and store it with an expiry."""
    token = secrets.token_urlsafe(32)
    _tokens[token] = time.time() + TOKEN_TTL
    return token


def revoke_token(token: str) -> None:
    """Remove a token from the active set."""
    _tokens.pop(token, None)


def is_valid_token(token: str) -> bool:
    """Return True if the token exists and has not expired."""
    expiry = _tokens.get(token)
    if expiry is None:
        return False
    if time.time() > expiry:
        _tokens.pop(token, None)
        return False
    return True


def require_auth(request: Request) -> None:
    """FastAPI dependency — raises 401 if the request has no valid token."""
    token = (
        request.headers.get("X-Session-Token")
        or request.cookies.get("spud_token")
    )
    # Also accept the long-lived CLI service token issued at install time.
    if token and CLI_TOKEN_FILE.exists():
        cli_token = CLI_TOKEN_FILE.read_text().strip()
        if cli_token and hmac.compare_digest(token, cli_token):
            return

    if not token or not is_valid_token(token):
        raise HTTPException(status_code=401, detail="Not authenticated")
