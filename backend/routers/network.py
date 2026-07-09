# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Network configuration routes.

Handles VLANs, WAN/router settings, static routes, and DNS entries.
All routes require authentication.
"""
import ipaddress
import secrets
import subprocess
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..models import DhcpReservation, DnsEntry, RouterConfig, StaticRoute, VlanConfig
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
    """Return network interfaces (excluding loopback)."""
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
            if name == "lo":
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


@router.put("/api/vlans/{vlan_id}")
def update_vlan(vlan_id: int, vlan: VlanConfig):
    if vlan.vlan_id != vlan_id:
        raise HTTPException(
            status_code=400,
            detail="vlan_id in body must match the VLAN ID in the URL",
        )

    state = load_state()
    vlans = state.get("vlans", [])
    idx = next((i for i, v in enumerate(vlans) if v["vlan_id"] == vlan_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"VLAN {vlan_id} not found")

    if any(
        i != idx and v["vlan_id"] == vlan.vlan_id and v["interface"] == vlan.interface
        for i, v in enumerate(vlans)
    ):
        raise HTTPException(
            status_code=400,
            detail=f"VLAN {vlan.vlan_id} on {vlan.interface} already exists",
        )

    vlans[idx] = vlan.model_dump()
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


# ── DHCP reservations (per-VLAN MAC→IP pinning) ──────────────────────────────

def _get_vlan_or_404(vlans: list[dict], vlan_id: int) -> dict:
    vlan = next((v for v in vlans if v["vlan_id"] == vlan_id), None)
    if vlan is None:
        raise HTTPException(status_code=404, detail=f"VLAN {vlan_id} not found")
    return vlan


def _validate_reservation(vlan: dict, reservation: DhcpReservation, exclude_id: str = None) -> None:
    """
    Cross-field/cross-record checks that need the owning VLAN's own state,
    so they live here rather than on the model: the reservation IP must
    fall inside the VLAN's subnet, and MAC/IP must be unique among the
    VLAN's *own* reservations (uniqueness is scoped per-VLAN, not global).
    """
    try:
        subnet = ipaddress.IPv4Network(f"{vlan['ip_address']}/{vlan['prefix_len']}", strict=False)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"VLAN {vlan['vlan_id']} has no valid subnet configured")

    if ipaddress.IPv4Address(reservation.ip) not in subnet:
        raise HTTPException(
            status_code=400,
            detail=f"Reservation IP {reservation.ip} is not within VLAN {vlan['vlan_id']}'s subnet ({subnet})",
        )

    others = [
        r for r in vlan.get("dhcp_reservations", [])
        if r.get("id") != exclude_id
    ]
    if any(r["mac"] == reservation.mac for r in others):
        raise HTTPException(
            status_code=400,
            detail=f"MAC {reservation.mac} already reserved on VLAN {vlan['vlan_id']}",
        )
    if any(r["ip"] == reservation.ip for r in others):
        raise HTTPException(
            status_code=400,
            detail=f"IP {reservation.ip} already reserved on VLAN {vlan['vlan_id']}",
        )


@router.get("/api/vlans/{vlan_id}/reservations")
def list_reservations(vlan_id: int):
    vlans = load_state().get("vlans", [])
    vlan = _get_vlan_or_404(vlans, vlan_id)
    return vlan.get("dhcp_reservations", [])


@router.post("/api/vlans/{vlan_id}/reservations")
def add_reservation(vlan_id: int, reservation: DhcpReservation):
    state = load_state()
    vlans = state.get("vlans", [])
    vlan = _get_vlan_or_404(vlans, vlan_id)

    _validate_reservation(vlan, reservation)

    reservation.id = secrets.token_hex(4)
    vlan.setdefault("dhcp_reservations", []).append(reservation.model_dump())
    save_state(state)
    return {"ok": True, "id": reservation.id}


@router.put("/api/vlans/{vlan_id}/reservations/{reservation_id}")
def update_reservation(vlan_id: int, reservation_id: str, reservation: DhcpReservation):
    state = load_state()
    vlans = state.get("vlans", [])
    vlan = _get_vlan_or_404(vlans, vlan_id)

    reservations = vlan.get("dhcp_reservations", [])
    idx = next((i for i, r in enumerate(reservations) if r.get("id") == reservation_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Reservation {reservation_id} not found on VLAN {vlan_id}")

    _validate_reservation(vlan, reservation, exclude_id=reservation_id)

    reservation.id = reservation_id
    reservations[idx] = reservation.model_dump()
    vlan["dhcp_reservations"] = reservations
    save_state(state)
    return {"ok": True, "id": reservation_id}


@router.delete("/api/vlans/{vlan_id}/reservations/{reservation_id}")
def delete_reservation(vlan_id: int, reservation_id: str):
    state = load_state()
    vlans = state.get("vlans", [])
    vlan = _get_vlan_or_404(vlans, vlan_id)

    before = len(vlan.get("dhcp_reservations", []))
    vlan["dhcp_reservations"] = [
        r for r in vlan.get("dhcp_reservations", []) if r.get("id") != reservation_id
    ]
    save_state(state)
    return {"removed": before - len(vlan["dhcp_reservations"])}


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
