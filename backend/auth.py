# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Authentication for spud-router.

Uses stateless HMAC-signed session tokens that survive service restarts and
reboots. A 32-byte secret is lazily generated and persisted to
/etc/spud-router/token-secret (mode 0o600) on first use.

Token format:  "{nonce}.{exp}.{sig}"
  nonce  — URL-safe base64 random (secrets.token_urlsafe(16))
  exp    — Unix timestamp (int) at which the token expires
  sig    — URL-safe base64 HMAC-SHA256 of "{nonce}:{exp}" with the secret

Authentication accepts the token from either:
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
import math
import os
import secrets
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException, Request

from .state import AUTH_FILE, SPUD_CONF, TOKEN_SECRET_FILE

TOKEN_TTL      = 8 * 3600   # 8 hours
CLI_TOKEN_FILE = SPUD_CONF / "cli-token"
_SESSION_STATE_FILE_NAME = "session-revocations.json"
_SESSION_LOCK_FILE_NAME = "session-revocations.lock"
_MAX_REVOKED_TOKENS = 4096

# scrypt parameters — tuned for ~100 ms on a 1 GHz ARM Cortex-A53
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_LEN = 16


# ── HMAC token secret ─────────────────────────────────────────────────────────

def _load_secret() -> bytes:
    """Return the server secret, creating it on first use."""
    if TOKEN_SECRET_FILE.exists():
        return TOKEN_SECRET_FILE.read_bytes()
    secret = os.urandom(32)
    TOKEN_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_SECRET_FILE.write_bytes(secret)
    TOKEN_SECRET_FILE.chmod(0o600)
    return secret


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


def check_current_password(password: str) -> bool:
    """Return True if password matches the currently stored credential."""
    _, stored_hash = _load_credentials()
    return _verify_password_hash(password, stored_hash)


def update_password(new_password: str) -> None:
    """Persist a new hash and invalidate every browser session.

    API keys and the CLI service token are deliberately separate credentials
    and remain valid; callers must log in again with the new password.
    """
    stored_user, _ = _load_credentials()
    _save_hash(stored_user, _hash_password(new_password))
    invalidate_all_sessions()


# ── Token management ──────────────────────────────────────────────────────────

# A process-local cache rejects a token without a disk read in the common
# same-process logout case. The persistent state below remains authoritative,
# so clearing this cache during a restart cannot resurrect a logged-out token.
_revoked: set[str] = set()


def _session_state_file() -> Path:
    """Return the persistent session state path (derived for test isolation)."""
    return SPUD_CONF / _SESSION_STATE_FILE_NAME


@contextmanager
def _session_lock():
    """Serialize persistent session-state reads and writes across workers."""
    import fcntl

    SPUD_CONF.mkdir(parents=True, exist_ok=True)
    with (SPUD_CONF / _SESSION_LOCK_FILE_NAME).open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _empty_session_state() -> dict:
    return {"invalid_before": 0.0, "revoked": {}}


def _read_session_state() -> dict:
    """Read persisted revocations, failing closed if the file is malformed."""
    path = _session_state_file()
    if not path.exists():
        return _empty_session_state()
    try:
        data = json.loads(path.read_text())
        invalid_before = float(data.get("invalid_before", 0))
        revoked = data.get("revoked", {})
        if not math.isfinite(invalid_before) or invalid_before < 0 or not isinstance(revoked, dict):
            raise ValueError("invalid session state")
        parsed_revocations = {str(digest): float(expiry) for digest, expiry in revoked.items()}
        if any(not math.isfinite(expiry) for expiry in parsed_revocations.values()):
            raise ValueError("invalid revocation expiry")
        return {
            "invalid_before": invalid_before,
            "revoked": parsed_revocations,
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        # Authentication must not silently become less strict when a state file
        # is damaged. Existing sessions are therefore rejected until repaired.
        return {"invalid_before": time.time(), "revoked": {}}


def _write_session_state(state: dict) -> None:
    """Atomically persist compact session state with owner-only permissions."""
    path = _session_state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".session-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _token_expiry(token: str) -> int | None:
    """Return a syntactically valid token's expiry without trusting its MAC."""
    try:
        _nonce, exp, _sig = token.split(".")
        return int(exp)
    except (TypeError, ValueError):
        return None


def _token_digest(token: str) -> str:
    """Store a one-way token identifier rather than the bearer token itself."""
    return hashlib.sha256(token.encode()).hexdigest()


def _prune_revocations(state: dict, now: float) -> bool:
    """Drop expired revocations and report whether persistent state changed."""
    kept = {
        digest: expiry
        for digest, expiry in state["revoked"].items()
        if expiry > now
    }
    changed = len(kept) != len(state["revoked"])
    state["revoked"] = kept
    return changed


def invalidate_all_sessions() -> None:
    """Invalidate every signed browser session now and after service restart."""
    with _session_lock():
        state = _read_session_state()
        state["invalid_before"] = time.time()
        _prune_revocations(state, time.time())
        _write_session_state(state)
    _revoked.clear()


def _sign(nonce: str, exp: str) -> str:
    """Return URL-safe base64 HMAC-SHA256 of '{nonce}:{exp}'."""
    mac = hmac.new(_load_secret(), f"{nonce}:{exp}".encode(), "sha256")
    return base64.urlsafe_b64encode(mac.digest()).rstrip(b"=").decode()


def create_token() -> str:
    """Issue a new stateless signed session token."""
    nonce = secrets.token_urlsafe(16)
    exp   = str(int(time.time()) + TOKEN_TTL)
    sig   = _sign(nonce, exp)
    return f"{nonce}.{exp}.{sig}"


def revoke_token(token: str) -> None:
    """Persistently revoke a token until its natural expiry."""
    _revoked.add(token)
    expiry = _token_expiry(token)
    if expiry is None:
        return
    with _session_lock():
        state = _read_session_state()
        now = time.time()
        _prune_revocations(state, now)
        if expiry > now:
            state["revoked"][_token_digest(token)] = expiry
            if len(state["revoked"]) > _MAX_REVOKED_TOKENS:
                # Do not allow repeated logout requests to grow persistent
                # state indefinitely. Invalidating the current generation is
                # safer than discarding an older token's revocation.
                state["invalid_before"] = now
                state["revoked"] = {}
            _write_session_state(state)


def is_valid_token(token: str) -> bool:
    """Return True if the token has a valid signature, has not expired, and has not been revoked."""
    if token in _revoked:
        return False
    try:
        nonce, exp, sig = token.split(".")
    except ValueError:
        return False
    expected = _sign(nonce, exp)
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        expiry = int(exp)
    except ValueError:
        return False
    now = time.time()
    with _session_lock():
        state = _read_session_state()
        pruned = _prune_revocations(state, now)
        if pruned:
            _write_session_state(state)
        if now >= expiry:
            return False
        # Token issue time is derivable because signed session TTL is fixed.
        if expiry - TOKEN_TTL < state["invalid_before"]:
            return False
        if _token_digest(token) in state["revoked"]:
            return False
    return True


class _AdminScopeContext:
    """Marker indicating a session-token (admin) caller with all scopes."""
    pass


def _required_scopes(request: Request) -> tuple[str, ...]:
    """Return the API-key scopes required for this REST request.

    Keeping this policy next to authentication prevents a newly-mounted
    router from authenticating a key but accidentally omitting authorization.
    Session and service-token principals remain administrative.
    """
    path = request.url.path
    method = request.method.upper()
    if path.startswith("/api/diagnostics/"):
        return ("diagnostics",)
    if path.startswith("/api/update/") or path.startswith("/api/apply") or path == "/api/system/reboot":
        return ("read",) if method == "GET" else ("apply",)
    if path.startswith(("/api/tailscale", "/api/wireguard", "/api/nebula", "/api/bgp")):
        return ("read",) if method == "GET" else ("vpn",)
    return ("read",) if method in ("GET", "HEAD", "OPTIONS") else ("write",)


def require_auth(request: Request) -> _AdminScopeContext | None:
    """FastAPI dependency — raises 401 if the request has no valid token.

    Accepts both session tokens (returns _AdminScopeContext, all scopes) and
    API keys (returns ApiKeyContext with limited scopes).
    """
    from . import api_keys as api_keys_module

    # Check Authorization header for Bearer token (API key)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer = auth_header[7:]
        if bearer.startswith("spud_"):
            ip = request.client.host if request.client else "unknown"
            if api_keys_module.check_rate_limit(ip):
                raise HTTPException(
                    status_code=429,
                    detail="Too many failed API key attempts — try again later",
                    headers={"Retry-After": "60"},
                )
            try:
                ctx = api_keys_module.validate_key(bearer)
            except ValueError as e:
                api_keys_module.record_failure(ip)
                raise HTTPException(status_code=401, detail=str(e))
            if ctx is None:
                api_keys_module.record_failure(ip)
                raise HTTPException(status_code=401, detail="Invalid API key")
            for scope in _required_scopes(request):
                if scope not in ctx.scopes:
                    raise HTTPException(status_code=403, detail=f"Missing required scope: {scope}")
            return ctx

    # Fall back to session token
    token = (
        request.headers.get("X-Session-Token")
        or request.cookies.get("spud_token")
    )
    if token and CLI_TOKEN_FILE.exists():
        cli_token = CLI_TOKEN_FILE.read_text().strip()
        if cli_token and hmac.compare_digest(token, cli_token):
            return _AdminScopeContext()

    if not token or not is_valid_token(token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    return _AdminScopeContext()


def require_session_token(request: Request) -> _AdminScopeContext:
    """
    FastAPI dependency — requires a session token, rejects API keys.

    Use this for sensitive operations like API key management where
    only admin session tokens (not scoped API keys) are permitted.
    """
    from . import api_keys as api_keys_module

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer = auth_header[7:]
        if bearer.startswith("spud_"):
            raise HTTPException(
                status_code=403,
                detail="API keys cannot perform this operation — session token required",
            )

    token = (
        request.headers.get("X-Session-Token")
        or request.cookies.get("spud_token")
    )
    if token and CLI_TOKEN_FILE.exists():
        cli_token = CLI_TOKEN_FILE.read_text().strip()
        if hmac.compare_digest(token, cli_token):
            return _AdminScopeContext()

    if not token or not is_valid_token(token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    return _AdminScopeContext()


def require_scope(*needed: str):
    """
    FastAPI dependency factory — requires that the authenticated principal
    has all of the listed scopes.

    Session tokens (browser/CLI) have all scopes implicitly.
    API keys are limited to the scopes assigned at creation time.

    Usage in a router endpoint:
        @router.post("/something", dependencies=[Depends(require_scope("write"))])
    """
    def checker(request: Request) -> None:
        ctx = require_auth(request)
        # Session tokens have all scopes
        if isinstance(ctx, _AdminScopeContext):
            return
        # API key: check scopes
        for scope in needed:
            if scope not in ctx.scopes:
                raise HTTPException(
                    status_code=403,
                    detail=f"Missing required scope: {scope}",
                )
    return checker
