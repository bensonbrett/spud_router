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
from ..models import ApplyRequest
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
        NETPLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
        NETPLAN_FILE.write_text(np)
        results.append(f"Written {NETPLAN_FILE}")

        DNSMASQ_FILE.parent.mkdir(parents=True, exist_ok=True)
        DNSMASQ_FILE.write_text(dm)
        results.append(f"Written {DNSMASQ_FILE}")

        IPTABLES_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        IPTABLES_SCRIPT.write_text(ipt)
        IPTABLES_SCRIPT.chmod(0o750)
        results.append(f"Written {IPTABLES_SCRIPT}")

        # Write hostapd config if wireless is enabled
        if hap:
            HOSTAPD_CONF.parent.mkdir(parents=True, exist_ok=True)
            HOSTAPD_CONF.write_text(hap)
            results.append(f"Written {HOSTAPD_CONF}")

        subprocess.run(["netplan", "apply"], check=True)
        results.append("netplan apply: OK")

        subprocess.run(["systemctl", "restart", "dnsmasq"], check=True)
        results.append("dnsmasq restart: OK")

        subprocess.run([str(IPTABLES_SCRIPT)], check=True)
        results.append("iptables: OK")

        # Start or stop hostapd based on wireless enabled state
        wireless = state.get("wireless", {})
        if wireless.get("enabled") and hap:
            subprocess.run(["systemctl", "enable", "--now", "hostapd"], check=True)
            subprocess.run(["systemctl", "restart", "hostapd"], check=True)
            results.append("hostapd restart: OK")
        else:
            # Stop hostapd if wireless was disabled
            subprocess.run(["systemctl", "stop", "hostapd"], check=False)
            subprocess.run(["systemctl", "disable", "hostapd"], check=False)

        results += tailscale_router.apply(state)

    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Command failed: {e}")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"File error: {e}")

    return {"ok": True, "steps": results}


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

    # Backfill optional keys with defaults
    defaults = empty_state()
    for key, default in defaults.items():
        data.setdefault(key, default)

    save_state(data)
    return {
        "ok":          True,
        "vlans":       len(data["vlans"]),
        "routes":      len(data["static_routes"]),
        "dns":         len(data["dns_entries"]),
        "fw_inbound":  len(data["fw_inbound"]),
        "fw_intervlan":len(data["fw_intervlan"]),
    }
