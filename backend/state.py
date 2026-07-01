"""
State management for spud-router.

The router's full configuration is stored as a single JSON file at
/etc/spud-router/state.json. All reads and writes go through load_state()
and save_state() — nothing else touches the file directly.
"""
import json
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SPUD_CONF          = Path("/etc/spud-router")
STATE_FILE         = SPUD_CONF / "state.json"
AUTH_FILE          = SPUD_CONF / "auth.json"
TOKEN_SECRET_FILE  = SPUD_CONF / "token-secret"
NETPLAN_FILE       = Path("/etc/netplan/50-spud-router.yaml")
DNSMASQ_FILE       = Path("/etc/dnsmasq.d/spud-router.conf")
IPTABLES_SCRIPT    = SPUD_CONF / "iptables.sh"


def empty_state() -> dict:
    """Return a fresh default state with all keys present."""
    return {
        "vlans": [],
        "router": {},
        "static_routes": [],
        "dns_entries": [],
        "fw_inbound": [],
        "fw_intervlan": [],
        "tailscale": {
            "enabled": False,
            "advertise_routes": [],
            "exit_node": False,
            "accept_routes": True,
        },
        "wireless": {
            "enabled": False,
            "interface": "wlan0",
            "country_code": "US",
            "ssids": [],
        },
    }


def load_state() -> dict:
    """
    Load state from disk, backfilling any keys added in newer versions.
    Safe to call at any time — returns empty_state() if the file is missing.
    """
    SPUD_CONF.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        return empty_state()

    try:
        data = json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        # Corrupted state file — return empty rather than crash
        return empty_state()

    # Backfill keys added in later versions so older state files still work
    defaults = empty_state()
    for key, default in defaults.items():
        data.setdefault(key, default)

    return data


def save_state(state: dict) -> None:
    """Atomically write state to disk."""
    SPUD_CONF.mkdir(parents=True, exist_ok=True)
    # Write to a temp file then rename for atomicity
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(STATE_FILE)
