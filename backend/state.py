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
import tempfile
from contextlib import contextmanager
from pathlib import Path

import fcntl

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


class StateCorruptionError(RuntimeError):
    """Raised instead of silently replacing an existing unreadable state file."""


@contextmanager
def _state_lock():
    """Serialize state file operations across service processes."""
    SPUD_CONF.mkdir(parents=True, exist_ok=True)
    with (SPUD_CONF / "state.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


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
            "use_relays": True,
            "relays": [],
            "am_relay": False,
            "cert_pem": "",
            "key_pem": "",
            "ca_pem": "",
            "firewall_inbound": [],
            "firewall_outbound": [{"port": "any", "proto": "any", "host": "any"}],
        },
        "bgp": {
            "enabled": False,
            "asn": None,
            "router_id": None,
            "neighbors": [],
            "networks": [],
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

    with _state_lock():
        try:
            data = json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise StateCorruptionError(
                f"Existing state file is unreadable; refusing to replace it: {exc}"
            ) from exc

    # Backfill keys added in later versions so older state files still work
    defaults = empty_state()
    for key, default in defaults.items():
        data.setdefault(key, default)

    return data


def save_state(state: dict) -> None:
    """Atomically write state to disk."""
    with _state_lock():
        fd, tmp_name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=SPUD_CONF)
        try:
            with os.fdopen(fd, "w") as tmp:
                json.dump(state, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(tmp_name, STATE_FILE)
        except Exception:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise
