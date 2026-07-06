# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
State management for spud-router.

The router's full configuration is stored as a single JSON file at
/etc/spud-router/state.json. All reads and writes go through load_state()
and save_state() — nothing else touches the file directly.
"""
import json
import os
import stat
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SPUD_CONF          = Path("/etc/spud-router")
STATE_FILE         = SPUD_CONF / "state.json"
AUTH_FILE          = SPUD_CONF / "auth.json"
TOKEN_SECRET_FILE  = SPUD_CONF / "token-secret"
TAILSCALE_AUTHKEY_FILE = SPUD_CONF / "tailscale-authkey"
NETPLAN_FILE       = Path("/etc/netplan/50-spud-router.yaml")
DNSMASQ_FILE       = Path("/etc/dnsmasq.d/spud-router.conf")
IPTABLES_SCRIPT    = SPUD_CONF / "iptables.sh"
APPLIED_SNAPSHOT_FILE = SPUD_CONF / "applied.json"
ROLLBACK_STATE_FILE   = SPUD_CONF / "state.rollback.json"     # revert target for the *currently-armed* apply (the state that was live before it) — cleared on confirm/revert
LAST_APPLIED_STATE_FILE = SPUD_CONF / "state.last-applied.json"  # full state as of the last successful apply — the "known-good" a future apply snapshots into ROLLBACK_STATE_FILE
ARM_STATUS_FILE       = SPUD_CONF / "arm-status.json"       # token/window for the currently-armed apply, if any
STAGING_FILE         = SPUD_CONF / "mcp-staging.json"       # staging buffer for MCP transactional pipeline


def empty_state() -> dict:
    """Return a fresh default state with all keys present."""
    return {
        "vlans": [],
        "router": {},
        "static_routes": [],
        "dns_entries": [],
        "fw_inbound": [],
        "fw_intervlan": [],
        "fw_outbound": [],
        "fw_outbound_default": "allow",   # "allow" | "deny" — fallback egress policy for LAN VLANs
        "port_forwards": [],
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
        "syslog": {
            "enabled": False,
            "server": "",
            "port": 514,
            "protocol": "udp",
            "facility": "*",
            "severity": "*",
            "keep_local": True,
        },
        "snmp": {
            "enabled": False,
            "version": "v2c",
            "community_ro": "",
            "community_rw": "",
            "allowlist": [],
            "bind_interface": "",
            "location": "",
            "contact": "",
        },
        "wireguard": {
            "enabled": False,
            "mode": "server",
            "listen_port": 51820,
            "private_key": "",
            "public_key": "",
            "address": "",
            "peers": [],
        },
        "nebula": {
            "enabled": False,
            "listen_port": 4242,
            "lighthouse_hosts": [],
            "static_host_map": {},
            "cert_pem": "",
            "key_pem": "",
            "ca_pem": "",
            "firewall_inbound": [],
            "firewall_outbound": [{"port": "any", "proto": "any", "host": "any"}],
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
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.rename(STATE_FILE)
