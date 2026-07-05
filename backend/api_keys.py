# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
API key management for spud-router.

Keys are stored in /etc/spud-router/api-keys.json as SHA-256 hashes.
The plaintext key is returned exactly once at creation time (WireGuard pattern).

Key format: spud_<40-char-random-hex>  (20 bytes of randomness)
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from .state import SPUD_CONF

API_KEYS_FILE = SPUD_CONF / "api-keys.json"

VALID_SCOPES = frozenset({"read", "write", "apply", "diagnostics", "vpn"})

# Rate limiting for failed API key validations
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = 10
_validation_failures: dict[str, list[float]] = {}


def _load_keys() -> dict:
    if not API_KEYS_FILE.exists():
        return {"keys": []}
    try:
        return json.loads(API_KEYS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"keys": []}


def _save_keys(data: dict) -> None:
    SPUD_CONF.mkdir(parents=True, exist_ok=True)
    tmp = API_KEYS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.rename(API_KEYS_FILE)


def _generate_key() -> tuple[str, str]:
    """Generate a new API key and its SHA-256 hash.

    Returns (plaintext_key, key_hash) where plaintext_key starts with 'spud_'.
    The plaintext is returned exactly once and never stored.
    """
    raw = secrets.token_bytes(20)
    plaintext = "spud_" + raw.hex()
    key_hash = "sha256:" + base64.b64encode(
        hashlib.sha256(plaintext.encode()).digest()
    ).decode()
    return plaintext, key_hash


def _hash_key(plaintext: str) -> str:
    """Compute the SHA-256 hash of a plaintext API key."""
    return "sha256:" + base64.b64encode(
        hashlib.sha256(plaintext.encode()).digest()
    ).decode()


@dataclass
class ApiKeyContext:
    """Result of a successful API key validation."""
    key_id: str
    name: str
    scopes: frozenset[str]


def create_key(name: str, scopes: list[str], expires_at: int | None = None) -> tuple[str, dict]:
    """Create a new API key.

    Returns (plaintext_key, stored_key_info). The plaintext key must be shown
    to the user exactly once and is never stored.
    """
    for scope in scopes:
        if scope not in VALID_SCOPES:
            raise ValueError(f"Invalid scope: {scope}")

    plaintext, key_hash = _generate_key()
    key_id = "ak_" + secrets.token_hex(4)

    now = time.time()
    stored = {
        "id": key_id,
        "name": name,
        "key_hash": key_hash,
        "scopes": list(scopes),
        "created_at": now,
        "expires_at": expires_at,
        "last_used": None,
    }

    data = _load_keys()
    data["keys"].append(stored)
    _save_keys(data)

    return plaintext, stored


def validate_key(plaintext: str) -> ApiKeyContext | None:
    """Validate an API key plaintext string.

    Returns ApiKeyContext on success, None on failure.
    Raises ValueError if the key is expired.
    """
    if not plaintext.startswith("spud_"):
        return None

    key_hash = _hash_key(plaintext)
    data = _load_keys()
    now = time.time()

    for key in data.get("keys", []):
        if not hmac.compare_digest(key_hash, key.get("key_hash", "")):
            continue

        # Check expiry
        expires_at = key.get("expires_at")
        if expires_at is not None and now > expires_at:
            raise ValueError("API key has expired")

        # Update last_used (best-effort)
        key["last_used"] = now
        try:
            _save_keys(data)
        except Exception:
            pass

        return ApiKeyContext(
            key_id=key["id"],
            name=key["name"],
            scopes=frozenset(key.get("scopes", [])),
        )

    return None


def record_failure(ip: str) -> None:
    """Record a failed API key validation for rate limiting."""
    now = time.time()
    failures = _validation_failures.setdefault(ip, [])
    failures.append(now)
    # Prune old entries
    cutoff = now - _RATE_LIMIT_WINDOW
    _validation_failures[ip] = [t for t in failures if t > cutoff]


def check_rate_limit(ip: str) -> bool:
    """Return True if the IP is rate-limited (too many recent failures)."""
    now = time.time()
    failures = _validation_failures.get(ip, [])
    cutoff = now - _RATE_LIMIT_WINDOW
    recent = [t for t in failures if t > cutoff]
    _validation_failures[ip] = recent
    return len(recent) >= _RATE_LIMIT_MAX


def list_keys() -> list[dict]:
    """List all API keys (no hashes exposed)."""
    data = _load_keys()
    result = []
    for key in data.get("keys", []):
        result.append({
            "id": key["id"],
            "name": key["name"],
            "scopes": key.get("scopes", []),
            "created_at": key.get("created_at"),
            "expires_at": key.get("expires_at"),
            "last_used": key.get("last_used"),
        })
    return result


def revoke_key(key_id: str) -> bool:
    """Revoke an API key by ID. Returns True if found and removed."""
    data = _load_keys()
    original = len(data["keys"])
    data["keys"] = [k for k in data["keys"] if k["id"] != key_id]
    if len(data["keys"]) == original:
        return False
    _save_keys(data)
    return True


def get_key_by_id(key_id: str) -> dict | None:
    """Get a key's stored data by ID (for admin use, hash not included)."""
    data = _load_keys()
    for key in data.get("keys", []):
        if key["id"] == key_id:
            return {
                "id": key["id"],
                "name": key["name"],
                "scopes": key.get("scopes", []),
                "created_at": key.get("created_at"),
                "expires_at": key.get("expires_at"),
                "last_used": key.get("last_used"),
            }
    return None
