"""
Network configuration routes.

Handles VLANs, WAN/router settings, static routes, and DNS entries.
All routes require authentication.
"""
import subprocess
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..models import DnsEntry, RouterConfig, StaticRoute, VlanConfig
from ..state import load_state, save_state

router = APIRouter(tags=["network"], dependencies=[Depends(require_auth)])


# ── System status ─────────────────────────────────────────────────────────────

@router.get("/api/system/status")
def system_status():
    """Return system status including whether a reboot is needed."""
    # Check if install happened after last boot
    version_file = Path("/opt/spud-router/VERSION")
    reboot_needed = False

    if version_file.exists():
        try:
            # Get install time (VERSION file modification time)
            install_time = version_file.stat().st_mtime

            # Get boot time from /proc/uptime
            with open("/proc/uptime") as f:
                uptime_seconds = float(f.read().split()[0])

            # Calculate boot time (current time - uptime)
            import time
            boot_time = time.time() - uptime_seconds

            # If install happened after boot, reboot is needed
            reboot_needed = install_time > boot_time
        except Exception:
            # If we can't determine, assume no reboot needed
            reboot_needed = False

    return {"reboot_needed": reboot_needed}


# ── Interfaces ────────────────────────────────────────────────────────────────

@router.get("/api/interfaces")
def list_interfaces():
    """Return physical network interfaces (excluding loopback and subinterfaces)."""
    try:
        result = subprocess.run(
            ["ip", "-br", "link", "show"],
            capture_output=True,
            text=True,
            check=True,
        )
        interfaces = []
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            name  = parts[0]
            if name == "lo" or "." in name:
                continue
            interfaces.append({
                "name":  name,
                "state": parts[1] if len(parts) > 1 else "UNKNOWN",
            })
        return interfaces
    except Exception:
        return []


# ── Router / WAN ──────────────────────────────────────────────────────────────

@router.get("/api/state")
def get_state():
    return load_state()


@router.post("/api/router")
def set_router(config: RouterConfig):
    state = load_state()
    state["router"] = config.model_dump()
    save_state(state)
    return {"ok": True}


# ── VLANs ─────────────────────────────────────────────────────────────────────

@router.get("/api/vlans")
def list_vlans():
    return load_state().get("vlans", [])


@router.post("/api/vlans")
def add_vlan(vlan: VlanConfig):
    state = load_state()
    vlans = state.get("vlans", [])

    if any(v["vlan_id"] == vlan.vlan_id and v["interface"] == vlan.interface for v in vlans):
        raise HTTPException(
            status_code=400,
            detail=f"VLAN {vlan.vlan_id} on {vlan.interface} already exists",
        )

    vlans.append(vlan.model_dump())
    state["vlans"] = vlans
    save_state(state)
    return {"ok": True}


@router.delete("/api/vlans/{vlan_id}")
def delete_vlan(vlan_id: int):
    state  = load_state()
    before = len(state.get("vlans", []))
    state["vlans"] = [v for v in state.get("vlans", []) if v["vlan_id"] != vlan_id]
    save_state(state)
    return {"removed": before - len(state["vlans"])}


# ── Static routes ─────────────────────────────────────────────────────────────

@router.get("/api/routes")
def list_routes():
    return load_state().get("static_routes", [])


@router.post("/api/routes")
def add_route(route: StaticRoute):
    state  = load_state()
    routes = state.get("static_routes", [])

    if any(r["destination"] == route.destination for r in routes):
        raise HTTPException(
            status_code=400,
            detail=f"Route to {route.destination} already exists",
        )

    routes.append(route.model_dump())
    state["static_routes"] = routes
    save_state(state)
    return {"ok": True}


@router.delete("/api/routes/{destination:path}")
def delete_route(destination: str):
    state  = load_state()
    before = len(state.get("static_routes", []))
    state["static_routes"] = [
        r for r in state.get("static_routes", []) if r["destination"] != destination
    ]
    save_state(state)
    return {"removed": before - len(state["static_routes"])}


# ── DNS entries ───────────────────────────────────────────────────────────────

@router.get("/api/dns")
def list_dns():
    return load_state().get("dns_entries", [])


@router.post("/api/dns")
def add_dns(entry: DnsEntry):
    state   = load_state()
    entries = state.get("dns_entries", [])

    if any(e["hostname"] == entry.hostname for e in entries):
        raise HTTPException(
            status_code=400,
            detail=f"DNS entry for {entry.hostname} already exists",
        )

    entries.append(entry.model_dump())
    state["dns_entries"] = entries
    save_state(state)
    return {"ok": True}


@router.delete("/api/dns/{hostname}")
def delete_dns(hostname: str):
    state  = load_state()
    before = len(state.get("dns_entries", []))
    state["dns_entries"] = [
        e for e in state.get("dns_entries", []) if e["hostname"] != hostname
    ]
    save_state(state)
    return {"removed": before - len(state["dns_entries"])}
