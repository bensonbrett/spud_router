"""
Config management routes: preview, apply, export, import, live status.
"""
import io
import json
import subprocess
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import require_auth
from ..generators import dnsmasq, hostapd, iptables, netplan
from ..models import (
    ApplyRequest, DnsEntry, InboundRule, InterVlanRule,
    RouterConfig, StaticRoute, TailscaleConfig, VlanConfig, WirelessConfig,
)
from ..state import (
    DNSMASQ_FILE,
    IPTABLES_SCRIPT,
    NETPLAN_FILE,
    empty_state,
    load_state,
    save_state,
)
from . import tailscale as tailscale_router

router = APIRouter(tags=["config"], dependencies=[Depends(require_auth)])


HOSTAPD_CONF = Path("/etc/hostapd/hostapd.conf")


@router.get("/api/preview")
def preview():
    """Return generated config files without writing anything to disk."""
    state = load_state()
    result = {
        "netplan":  netplan.generate(state),
        "dnsmasq":  dnsmasq.generate(state),
        "iptables": iptables.generate(state),
    }
    hostapd_conf = hostapd.generate(state)
    if hostapd_conf:
        result["hostapd"] = hostapd_conf
    return result


@router.post("/api/apply")
def apply(req: ApplyRequest):
    """Generate config files, write them to disk, and activate them."""
    state = load_state()
    np    = netplan.generate(state)
    dm    = dnsmasq.generate(state)
    ipt   = iptables.generate(state)
    hap   = hostapd.generate(state)

    if req.dry_run:
        result = {"dry_run": True, "netplan": np, "dnsmasq": dm, "iptables": ipt}
        if hap:
            result["hostapd"] = hap
        return result

    results = []
    try:
        # Write netplan config via sudo tee (root-owned directory)
        subprocess.run(
            ["sudo", "tee", str(NETPLAN_FILE)],
            input=np, text=True, check=True, capture_output=True,
        )
        results.append(f"Written {NETPLAN_FILE}")

        # Write dnsmasq config via sudo tee (root-owned directory)
        subprocess.run(
            ["sudo", "tee", str(DNSMASQ_FILE)],
            input=dm, text=True, check=True, capture_output=True,
        )
        results.append(f"Written {DNSMASQ_FILE}")

        # Write iptables script directly (/etc/spud-router/ is service-user writable)
        IPTABLES_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        IPTABLES_SCRIPT.write_text(ipt)
        IPTABLES_SCRIPT.chmod(0o750)
        results.append(f"Written {IPTABLES_SCRIPT}")

        # Write hostapd config via sudo tee (root-owned directory)
        if hap:
            subprocess.run(
                ["sudo", "tee", str(HOSTAPD_CONF)],
                input=hap, text=True, check=True, capture_output=True,
            )
            results.append(f"Written {HOSTAPD_CONF}")

        subprocess.run(["sudo", "netplan", "apply"], check=True, capture_output=True, text=True)
        results.append("netplan apply: OK")

        subprocess.run(["sudo", "systemctl", "restart", "dnsmasq"], check=True, capture_output=True, text=True)
        results.append("dnsmasq restart: OK")

        proc = subprocess.run(["sudo", "bash", str(IPTABLES_SCRIPT)], check=True, capture_output=True, text=True)
        if proc.stderr.strip():
            results.append(f"iptables: OK (stderr: {proc.stderr.strip()})")
        else:
            results.append("iptables: OK")

        # Start or stop hostapd based on wireless enabled state
        wireless = state.get("wireless", {})
        if wireless.get("enabled") and hap:
            subprocess.run(["sudo", "systemctl", "enable", "--now", "hostapd"], check=True, capture_output=True, text=True)
            subprocess.run(["sudo", "systemctl", "restart", "hostapd"], check=True, capture_output=True, text=True)
            results.append("hostapd restart: OK")
        else:
            # Stop hostapd if wireless was disabled
            subprocess.run(["sudo", "systemctl", "stop", "hostapd"], check=False, capture_output=True, text=True)
            subprocess.run(["sudo", "systemctl", "disable", "hostapd"], check=False, capture_output=True, text=True)

        results += tailscale_router.apply(state)

    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        detail = f"Command failed: {' '.join(str(a) for a in e.cmd)} (exit {e.returncode})"
        if stderr:
            detail += f": {stderr}"
        raise HTTPException(status_code=500, detail=detail)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"File error: {e}")

    return {"ok": True, "steps": results}


@router.get("/api/diagnostics")
def diagnostics():
    """
    Return per-interface link and address diagnostics for all configured
    VLANs and the WAN interface. Includes switch-side PVID hints when a
    VLAN or WAN interface has no IP address despite a carrier being present.
    """
    state  = load_state()
    vlans  = state.get("vlans", [])
    router_cfg = state.get("router", {})
    wan_if = router_cfg.get("wan_interface", "")

    def _sysfs(iface: str, attr: str) -> str:
        try:
            return Path(f"/sys/class/net/{iface}/{attr}").read_text().strip()
        except OSError:
            return "unknown"

    def _carrier(iface: str) -> bool | None:
        raw = _sysfs(iface, "carrier")
        if raw == "1":
            return True
        if raw == "0":
            return False
        return None  # interface doesn't exist

    def _operstate(iface: str) -> str:
        return _sysfs(iface, "operstate")

    def _addresses(iface: str) -> list[str]:
        try:
            import json as _json
            result = subprocess.run(
                ["ip", "-j", "addr", "show", iface],
                capture_output=True, text=True,
            )
            data = _json.loads(result.stdout or "[]")
            addrs = []
            for entry in data:
                for info in entry.get("addr_info", []):
                    if info.get("family") in ("inet", "inet6"):
                        addrs.append(f"{info['local']}/{info['prefixlen']}")
            return addrs
        except Exception:
            return []

    def _default_route() -> str:
        try:
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True, text=True,
            )
            return result.stdout.strip()
        except Exception:
            return ""

    def _leases_for(iface_prefix: str) -> list[dict]:
        lease_file = Path("/var/lib/misc/dnsmasq.leases")
        leases: list[dict] = []
        if not lease_file.exists():
            return leases
        for line in lease_file.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 4:
                leases.append({"mac": parts[1], "ip": parts[2], "hostname": parts[3]})
        return leases

    default_route_raw = _default_route()

    # WAN
    wan_info: dict | None = None
    if wan_if:
        wan_addrs   = _addresses(wan_if)
        wan_carrier = _carrier(wan_if)
        is_default  = wan_if in default_route_raw
        hint = None
        if wan_carrier is True and not wan_addrs:
            wan_vlan_id = wan_if.rsplit(".", 1)[-1] if "." in wan_if else None
            if wan_vlan_id:
                hint = f"Carrier is up but no IP — check that the switch port for {wan_if} has PVID {wan_vlan_id}."
            else:
                hint = "Carrier is up but no IP — DHCP may have failed; check the upstream switch port."
        wan_info = {
            "name":             wan_if,
            "role":             "wan",
            "carrier":          wan_carrier,
            "operstate":        _operstate(wan_if),
            "addresses":        wan_addrs,
            "is_default_gw":    is_default,
            "hint":             hint,
        }

    # VLANs
    vlan_items = []
    all_leases = _leases_for("")
    for v in vlans:
        subif   = f"{v['interface']}.{v['vlan_id']}"
        addrs   = _addresses(subif)
        carrier = _carrier(subif)
        # A VLAN with no static IP (e.g. the WAN VLAN) is addressed by DHCP;
        # don't synthesize a meaningless "/0" configured address for it.
        cfg_ip  = f"{v['ip_address']}/{v['prefix_len']}" if v["ip_address"] else None
        ip_present = bool(cfg_ip) and cfg_ip in addrs
        hint = None
        if carrier is True and not addrs:
            if cfg_ip:
                hint = (
                    f"Carrier is up but {cfg_ip} is not assigned — "
                    f"check trunk port carries VLAN {v['vlan_id']} and "
                    f"the access port PVID is set to {v['vlan_id']}."
                )
            else:
                hint = (
                    f"Carrier is up but no IP acquired — check that VLAN "
                    f"{v['vlan_id']} is trunked to this port and DHCP is available."
                )
        # Attribute leases to this VLAN by its subnet prefix. Only meaningful
        # when the VLAN has a static IP; an empty prefix would match every
        # lease. Anchor on a trailing dot so 192.168.1.x doesn't swallow
        # 192.168.10.x.
        if v["ip_address"]:
            subnet_prefix = v["ip_address"].rsplit(".", 1)[0] + "."
            vlan_leases   = [l for l in all_leases if l["ip"].startswith(subnet_prefix)]
        else:
            vlan_leases = []
        vlan_items.append({
            "name":        subif,
            "vlan_id":     v["vlan_id"],
            "vlan_name":   v["name"],
            "role":        "vlan",
            "carrier":     carrier,
            "operstate":   _operstate(subif),
            "addresses":   addrs,
            "cfg_address": cfg_ip,
            "ip_present":  ip_present,
            "leases":      vlan_leases,
            "hint":        hint,
        })

    return {
        "wan":           wan_info,
        "vlans":         vlan_items,
        "default_route": default_route_raw,
    }


@router.get("/api/status")
def system_status():
    """Return live interface state, routing table, and DHCP leases."""
    def run(*cmd) -> str:
        try:
            return subprocess.run(list(cmd), capture_output=True, text=True).stdout
        except Exception:
            return ""

    leases = []
    lease_file = Path("/var/lib/misc/dnsmasq.leases")
    if lease_file.exists():
        for line in lease_file.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 4:
                leases.append({
                    "mac":      parts[1],
                    "ip":       parts[2],
                    "hostname": parts[3],
                })

    return {
        "interfaces": run("ip", "-br", "addr"),
        "routes":     run("ip", "route"),
        "leases":     leases,
    }


@router.get("/api/config/export")
def export_config():
    """
    Download a zip archive containing:
      - spud-router-state.json  (importable)
      - netplan/50-spud-router.yaml
      - dnsmasq/spud-router.conf
      - iptables/iptables.sh
      - README.txt
    """
    state = load_state()
    buf   = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("spud-router-state.json", json.dumps(state, indent=2))
        zf.writestr("netplan/50-spud-router.yaml", netplan.generate(state))
        zf.writestr("dnsmasq/spud-router.conf",    dnsmasq.generate(state))
        zf.writestr("iptables/iptables.sh",         iptables.generate(state))
        zf.writestr("README.txt", (
            f"spud-router config export\n"
            f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n\n"
            f"To restore: POST spud-router-state.json to /api/config/import\n"
            f"Then click Apply in the web UI to push live.\n"
        ))

    buf.seek(0)
    filename = f"spud-router-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/config/import")
async def import_config(request: Request):
    """Restore state from an uploaded JSON backup."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")

    for required_key in ("router", "vlans"):
        if required_key not in data:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required key: {required_key}",
            )

    # Validate every section through its Pydantic model so that field-level
    # validators (interface names, IPs, description sanitisation) all run on
    # imported data exactly as they would on direct API calls.
    try:
        validated: dict = empty_state()

        if data.get("router"):
            validated["router"] = RouterConfig(**data["router"]).model_dump()

        validated["vlans"] = [VlanConfig(**v).model_dump() for v in data.get("vlans", [])]
        validated["static_routes"] = [StaticRoute(**r).model_dump() for r in data.get("static_routes", [])]
        validated["dns_entries"] = [DnsEntry(**e).model_dump() for e in data.get("dns_entries", [])]
        validated["fw_inbound"] = [InboundRule(**r).model_dump() for r in data.get("fw_inbound", [])]
        validated["fw_intervlan"] = [InterVlanRule(**r).model_dump() for r in data.get("fw_intervlan", [])]

        if data.get("tailscale"):
            validated["tailscale"] = TailscaleConfig(**data["tailscale"]).model_dump()

        if data.get("wireless"):
            validated["wireless"] = WirelessConfig(**data["wireless"]).model_dump()

    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Validation error in imported config: {exc}")

    save_state(validated)
    return {
        "ok":           True,
        "vlans":        len(validated["vlans"]),
        "routes":       len(validated["static_routes"]),
        "dns":          len(validated["dns_entries"]),
        "fw_inbound":   len(validated["fw_inbound"]),
        "fw_intervlan": len(validated["fw_intervlan"]),
    }
