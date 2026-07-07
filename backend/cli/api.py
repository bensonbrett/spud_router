# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
CLI API client.

Thin wrapper around urllib so spud-cli has zero pip dependencies beyond
the stdlib. Token is persisted to /etc/spud-router/cli-token so the user
isn't prompted on every SSH session.
"""
import http.cookiejar
import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path

# The backend serves HTTPS with a self-signed cert (see the systemd unit's
# --ssl-keyfile/--ssl-certfile and install.sh's openssl-generated cert). So we
# must (1) talk https, not http, and (2) skip cert verification for the box's
# own self-signed cert — otherwise every CLI call fails (http→TLS port is
# refused; a verifying context raises CERTIFICATE_VERIFY_FAILED). Mirrors how
# update.py's health check reaches the same local endpoint.
API_BASE   = "https://127.0.0.1:8080"
TOKEN_FILE = Path("/etc/spud-router/cli-token")

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_token: str | None = None


def load_token() -> None:
    """Load a previously saved session token from disk."""
    global _token
    if TOKEN_FILE.exists():
        _token = TOKEN_FILE.read_text().strip() or None


def save_token(token: str) -> None:
    """Persist a session token to disk."""
    global _token
    _token = token
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)
    TOKEN_FILE.chmod(0o600)


def clear_token() -> None:
    """Remove the persisted token."""
    global _token
    _token = None
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def get_token() -> str | None:
    return _token


def request(method: str, path: str, body=None):
    """
    Make an authenticated request to the backend API.

    Raises RuntimeError with a human-readable message on any failure.
    """
    url  = API_BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if _token:
        headers["X-Session-Token"] = _token

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            detail = json.loads(raw).get("detail", raw)
        except Exception:
            detail = raw
        raise RuntimeError(detail)
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Cannot reach backend ({e.reason})\n"
            f"  Is spud-router running?  systemctl status spud-router"
        )


def GET(path: str):
    return request("GET", path)


def POST(path: str, body=None):
    return request("POST", path, body)


def PUT(path: str, body=None):
    return request("PUT", path, body)


def DELETE(path: str):
    return request("DELETE", path)


def login(username: str, password: str) -> str:
    """Login and return the session token from the Set-Cookie header."""
    url  = API_BASE + "/api/auth/login"
    data = json.dumps({"username": username, "password": password}).encode()
    req  = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            cj = http.cookiejar.CookieJar()
            cj.extract_cookies(resp, req)
            for cookie in cj:
                if cookie.name == "spud_token":
                    return cookie.value
            # Fallback: parse Set-Cookie header directly
            set_cookie = resp.headers.get("Set-Cookie", "")
            for part in set_cookie.split(";"):
                if "spud_token=" in part:
                    return part.split("spud_token=", 1)[1].strip()
            raise RuntimeError("No spud_token in login response")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            detail = json.loads(raw).get("detail", raw)
        except Exception:
            detail = raw
        raise RuntimeError(detail)
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Cannot reach backend ({e.reason})\n"
            f"  Is spud-router running?  systemctl status spud-router"
        )
