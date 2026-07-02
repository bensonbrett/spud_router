"""
CLI API client.

Thin wrapper around urllib so spud-cli has zero pip dependencies beyond
the stdlib. Token is persisted to /etc/spud-router/cli-token so the user
isn't prompted on every SSH session.
"""
import json
import urllib.error
import urllib.request
from pathlib import Path

API_BASE   = "http://127.0.0.1:8080"
TOKEN_FILE = Path("/etc/spud-router/cli-token")

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
        with urllib.request.urlopen(req, timeout=10) as resp:
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
