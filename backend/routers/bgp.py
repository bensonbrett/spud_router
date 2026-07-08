# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
BGP (FRR) configuration and live session status routes (issue #143).

No secrets here (unlike SNMP/WireGuard/VPN credentials) — ASN, router-id,
and neighbor IPs are not sensitive, so GET/PUT is a plain round-trip with
no masking.

/api/bgp/status runs two FIXED vtysh commands — argv lists with no string
interpolation of any kind, so there is no command-injection surface
regardless of what's in state["bgp"]. Never build the vtysh command from
user input.
"""
import json
import subprocess

from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..models import BgpConfig
from ..state import load_state, save_state

router = APIRouter(prefix="/api/bgp", tags=["bgp"], dependencies=[Depends(require_auth)])

_VTYSH_TIMEOUT = 5


def _vtysh_json(*args: str) -> dict | None:
    """Run a fixed vtysh command and parse its JSON output. Returns None on
    any failure (frr not installed, not running, unexpected output) — the
    caller degrades gracefully rather than erroring the whole status call."""
    try:
        proc = subprocess.run(
            ["vtysh", "-c", " ".join(args)],
            capture_output=True, text=True, timeout=_VTYSH_TIMEOUT,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


@router.get("")
def get_bgp():
    state = load_state()
    return state.get("bgp", BgpConfig().model_dump())


@router.put("")
def set_bgp(config: BgpConfig):
    state = load_state()
    state["bgp"] = config.model_dump()
    save_state(state)
    return {"ok": True}


@router.get("/status")
def get_status():
    """
    Live BGP session status via vtysh — degrades gracefully whenever frr
    isn't installed/running/configured for BGP (empty neighbor list, not
    an error), since this is polled continuously by the Routes tab.
    """
    state = load_state()
    bgp = state.get("bgp", {})
    if not bgp.get("enabled"):
        return {"enabled": False, "running": False, "neighbors": []}

    summary = _vtysh_json("show", "ip", "bgp", "summary", "json")
    if summary is None:
        return {"enabled": True, "running": False, "neighbors": []}

    peers = (summary.get("ipv4Unicast") or {}).get("peers") or {}

    # Advertised-prefix counts aren't in the summary — a second, equally
    # fixed vtysh call for per-neighbor detail carries them.
    detail = _vtysh_json("show", "ip", "bgp", "neighbors", "json") or {}

    neighbors = []
    for ip, peer in peers.items():
        pfx_sent = None
        peer_detail = detail.get(ip)
        if peer_detail:
            afi = (peer_detail.get("addressFamilyInfo") or {}).get("ipv4Unicast") or {}
            pfx_sent = afi.get("sentPrefixCounter")
        neighbors.append({
            "ip": ip,
            "state": peer.get("state", "Unknown"),
            "pfx_rcvd": peer.get("pfxRcd"),
            "pfx_sent": pfx_sent,
        })

    return {"enabled": True, "running": True, "neighbors": neighbors}
