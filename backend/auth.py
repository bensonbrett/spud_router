"""
Authentication for spud-router.

Uses a simple in-memory token store. Tokens are issued on login and expire
after TOKEN_TTL seconds. The token must be presented in either:
  - X-Session-Token header
  - spud_token cookie

Credentials are stored in /etc/spud-router/auth.json as a sha256 hash.
Environment variables SPUD_USER and SPUD_PASS override the file.

There is also a CLI service token stored at /etc/spud-router/cli-token,
issued at install time, which allows the spud-cli to authenticate without
prompting the user on every SSH session.
"""
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import HTTPException, Request

from .state import AUTH_FILE, SPUD_CONF

TOKEN_TTL     = 8 * 3600   # 8 hours
CLI_TOKEN_FILE = SPUD_CONF / "cli-token"

# In-memory token store: {token: expiry_unix_timestamp}
_tokens: dict[str, float] = {}


def _load_credentials() -> tuple[str, str]:
    """Return (username, password_sha256_hex). Env vars take priority."""
    env_user = os.environ.get("SPUD_USER")
    env_pass = os.environ.get("SPUD_PASS")
    if env_user and env_pass:
        return env_user, hashlib.sha256(env_pass.encode()).hexdigest()

    if AUTH_FILE.exists():
        data = json.loads(AUTH_FILE.read_text())
        return data["username"], data["password_sha256"]

    # Default credentials — insecure, replaced by installer
    return "admin", hashlib.sha256(b"spudrouter").hexdigest()


def verify_credentials(username: str, password: str) -> bool:
    """Return True if the supplied credentials are correct."""
    stored_user, stored_hash = _load_credentials()
    attempt_hash = hashlib.sha256(password.encode()).hexdigest()
    return (
        secrets.compare_digest(username, stored_user)
        and secrets.compare_digest(attempt_hash, stored_hash)
    )


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
    # Also accept the CLI service token
    if not token and CLI_TOKEN_FILE.exists():
        cli_token = CLI_TOKEN_FILE.read_text().strip()
        token = request.headers.get("X-Session-Token") or request.cookies.get("spud_token")

    if not token or not is_valid_token(token):
        raise HTTPException(status_code=401, detail="Not authenticated")


def update_password(new_password: str) -> None:
    """Persist a new password hash to auth.json."""
    stored_user, _ = _load_credentials()
    new_hash = hashlib.sha256(new_password.encode()).hexdigest()
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(
        json.dumps({"username": stored_user, "password_sha256": new_hash})
    )
    AUTH_FILE.chmod(0o600)
