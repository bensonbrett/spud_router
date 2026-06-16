"""
Wireless configuration routes.

Handles wireless AP settings and SSID management.
Also provides interface capability detection so the UI can warn the user
if their hardware doesn't support AP mode or multiple virtual interfaces.
"""
import re
import secrets
import subprocess

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..models import WirelessConfig, WirelessSsid
from ..state import load_state, save_state

router = APIRouter(
    prefix="/api/wireless",
    tags=["wireless"],
    dependencies=[Depends(require_auth)],
)


# ── Hardware capability detection ─────────────────────────────────────────────

def _detect_wireless_interfaces() -> list[dict]:
    """
    Return a list of wireless interfaces with capability info.

    Each dict contains:
        name:        interface name (e.g. wlan0)
        supports_ap: bool — interface supports AP mode
        max_vaps:    int  — max simultaneous virtual APs (0 if unknown)
        driver:      str  — kernel driver name
        phy:         str  — physical device (e.g. phy0)
    """
    interfaces = []
    try:
        # List wireless interfaces via iw
        result = subprocess.run(
            ["iw", "dev"],
            capture_output=True, text=True, timeout=5,
        )
        current_phy  = None
        current_if   = None
        for line in result.stdout.splitlines():
            phy_match = re.match(r'^phy#(\d+)', line)
            if phy_match:
                current_phy = f"phy{phy_match.group(1)}"

            if_match = re.match(r'\s+Interface\s+(\S+)', line)
            if if_match:
                current_if = if_match.group(1)
                if current_phy:
                    interfaces.append({
                        "name":        current_if,
                        "phy":         current_phy,
                        "supports_ap": False,
                        "max_vaps":    0,
                        "driver":      "",
                    })
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    # Check AP mode support and max VAPs for each interface
    for iface in interfaces:
        try:
            phy_info = subprocess.run(
                ["iw", "phy", iface["phy"], "info"],
                capture_output=True, text=True, timeout=5,
            ).stdout

            # Check if AP mode is listed in supported interface modes
            if "AP" in phy_info and "Supported interface modes" in phy_info:
                modes_section = phy_info.split("Supported interface modes")[1].split("\n\n")[0]
                iface["supports_ap"] = "* AP" in modes_section or " AP\n" in modes_section

            # Count max virtual interfaces of AP type
            vif_match = re.search(r'valid interface combinations:.*?#{ AP } <= (\d+)', phy_info, re.DOTALL)
            if vif_match:
                iface["max_vaps"] = int(vif_match.group(1))
            elif iface["supports_ap"]:
                iface["max_vaps"] = 1  # conservative default

            # Get driver name
            driver_result = subprocess.run(
                ["readlink", f"/sys/class/net/{iface['name']}/device/driver"],
                capture_output=True, text=True,
            )
            if driver_result.returncode == 0:
                iface["driver"] = driver_result.stdout.strip().split("/")[-1]

        except Exception:
            pass

    return interfaces


@router.get("/interfaces")
def list_wireless_interfaces():
    """
    Return detected wireless interfaces with AP capability info.
    Used by the UI to populate the interface selector and show warnings.
    """
    return _detect_wireless_interfaces()


# ── Config ────────────────────────────────────────────────────────────────────

@router.get("")
def get_wireless():
    return load_state().get("wireless", {})


@router.post("")
def set_wireless(config: WirelessConfig):
    state = load_state()
    state["wireless"] = config.model_dump()
    save_state(state)
    return {"ok": True}


# ── SSIDs ─────────────────────────────────────────────────────────────────────

@router.get("/ssids")
def list_ssids():
    return load_state().get("wireless", {}).get("ssids", [])


@router.post("/ssids")
def add_ssid(ssid: WirelessSsid):
    state    = load_state()
    wireless = state.get("wireless", {})
    ssids    = wireless.get("ssids", [])

    # Validate: only one SSID per VLAN
    if any(s["vlan_id"] == ssid.vlan_id for s in ssids):
        raise HTTPException(
            status_code=400,
            detail=f"An SSID for VLAN {ssid.vlan_id} already exists",
        )

    # Validate: password required for non-open security
    if ssid.security != "open" and len(ssid.password) < 8:
        raise HTTPException(
            status_code=400,
            detail="WPA password must be at least 8 characters",
        )

    ssid.id = secrets.token_hex(4)
    ssids.append(ssid.model_dump())
    wireless["ssids"] = ssids
    state["wireless"] = wireless
    save_state(state)
    return {"ok": True, "id": ssid.id}


@router.put("/ssids/{ssid_id}")
def update_ssid(ssid_id: str, ssid: WirelessSsid):
    state    = load_state()
    wireless = state.get("wireless", {})
    ssids    = wireless.get("ssids", [])

    idx = next((i for i, s in enumerate(ssids) if s.get("id") == ssid_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"SSID {ssid_id} not found")

    if ssid.security != "open" and len(ssid.password) < 8:
        raise HTTPException(
            status_code=400,
            detail="WPA password must be at least 8 characters",
        )

    ssid.id = ssid_id
    ssids[idx] = ssid.model_dump()
    wireless["ssids"] = ssids
    state["wireless"] = wireless
    save_state(state)
    return {"ok": True}


@router.delete("/ssids/{ssid_id}")
def delete_ssid(ssid_id: str):
    state    = load_state()
    wireless = state.get("wireless", {})
    before   = len(wireless.get("ssids", []))
    wireless["ssids"] = [s for s in wireless.get("ssids", []) if s.get("id") != ssid_id]
    state["wireless"] = wireless
    save_state(state)
    return {"removed": before - len(wireless["ssids"])}
