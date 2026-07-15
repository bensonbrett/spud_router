# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Config management routes: preview, apply, export, import, live status.
"""
import hashlib
import io
import json
import secrets
import subprocess
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from .. import apply_core
from ..auth import require_auth
from ..generators import dnsmasq, iptables, netplan
from ..models import (
    ApplyConfirmRequest, ApplyRequest, BgpConfig, DnsEntry, InboundRule,
    InterVlanRule, NebulaConfig, OutboundRule, PortForward, RouterConfig,
    SNMP_MASKED_SENTINEL, SnmpConfig, StaticRoute, SyslogConfig,
    TailscaleConfig, VlanConfig, WirelessConfig, WireguardConfig,
)
from ..state import (
    APPLIED_SNAPSHOT_FILE,
    ARM_STATUS_FILE,
    LAST_APPLIED_STATE_FILE,
    ROLLBACK_STATE_FILE,
    empty_state,
    load_state,
    save_state,
)
from ..update import SPUD_COMMIT_SCRIPT

router = APIRouter(tags=["config"], dependencies=[Depends(require_auth)])

# Every real (non-dry-run) Apply is armed with an auto-revert watchdog —
# see apply()'s docstring for why "always arm" was chosen over classifying
# which changes are "connectivity-affecting".
ARM_WINDOW_SECONDS = 90


def _mask_snmp_preview(text: str, state: dict) -> str:
    """Replace cleartext community strings with the masked sentinel before
    returning generated snmpd.conf content to the UI (preview/dry-run only —
    the real file written to disk by apply() keeps the real values, since
    snmpd itself needs them in cleartext)."""
    if not text:
        return text
    snmp = state.get("snmp", {})
    for community in (snmp.get("community_ro"), snmp.get("community_rw")):
        if community:
            text = text.replace(community, SNMP_MASKED_SENTINEL)
    return text


# The connectivity-affecting generators — everything a manual Apply
# activates except the sysctl drop-in. Numerically this is exactly what
# this module's pre-#184 _generated_hash() hashed: sysctls were always
# embedded inside the iptables text rather than a separately hashed
# component, so this key set is unchanged by the #184 refactor — only
# where the sysctl content itself now lives (generators/sysctl.py).
UNSAFE_GENERATOR_KEYS = (
    "netplan", "dnsmasq", "iptables", "hostapd", "syslog", "snmp", "doh", "bgp",
)


def _unsafe_hash(state: dict) -> str:
    """
    Hash of the *generated* connectivity-affecting config output (not raw
    state.json). Cosmetic state edits that don't change any emitted config
    correctly read as "nothing to apply" — this deliberately doesn't hash
    state itself, since e.g. reordering an unrelated list would otherwise
    cause a false "pending" reading. Drift here always requires a manual
    Apply — #184's OTA guarded auto-apply never touches this bucket.
    """
    generated = apply_core.generate_all(state)
    parts = [generated[k] or "" for k in UNSAFE_GENERATOR_KEYS]
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def _safe_hash(state: dict) -> str:
    """
    Hash of the *generated* connectivity-safe config output — the sysctl
    drop-in only. The only bucket #184's OTA guarded auto-apply
    (apply_core.activate_safe_subset()) is allowed to activate unattended;
    see generators/sysctl.py's docstring for why this exact pair can never
    affect management reachability.
    """
    generated = apply_core.generate_all(state)
    return hashlib.sha256((generated["sysctl"] or "").encode()).hexdigest()


def _write_applied_snapshot(state: dict) -> None:
    """
    Record what was actually pushed live, split into the safe (sysctl) and
    unsafe (everything else) buckets — v2 format (#184). update.py's
    guarded auto-apply needs both hashes separately so it can tell "only
    safe drift" from "unsafe drift" after an OTA; /api/apply/status ORs
    them back together for its single "pending" boolean, since a manual
    apply always activates both buckets regardless of which one drifted.
    """
    APPLIED_SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    APPLIED_SNAPSHOT_FILE.write_text(json.dumps({
        "safe_hash":   _safe_hash(state),
        "unsafe_hash": _unsafe_hash(state),
    }))


def _promote_to_last_applied(state: dict) -> None:
    """
    Record `state` as the known-good baseline that the *next* apply's
    rollback snapshot is taken from (see apply()'s docstring for the full
    picture). Called when an apply is confirmed, and also immediately for
    any apply that ends up unarmed (first-ever apply, or arming itself
    failed) — in both of those cases there is no confirm step coming to
    promote it later, so this apply's own state becomes the baseline right
    away instead of leaving LAST_APPLIED_STATE_FILE stale forever.
    """
    LAST_APPLIED_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_APPLIED_STATE_FILE.write_text(json.dumps(state, indent=2))


@router.get("/api/preview")
def preview():
    """Return generated config files without writing anything to disk."""
    state = load_state()
    generated = apply_core.generate_all(state)
    result = {
        "netplan":  generated["netplan"],
        "dnsmasq":  generated["dnsmasq"],
        "iptables": generated["iptables"],
    }
    if generated["hostapd"]:
        result["hostapd"] = generated["hostapd"]
    if generated["syslog"]:
        result["syslog"] = generated["syslog"]
    if generated["snmp"]:
        result["snmp"] = _mask_snmp_preview(generated["snmp"], state)
    if generated["doh"]:
        result["doh"] = generated["doh"]
    if generated["bgp"]:
        result["bgp"] = generated["bgp"]
    return result


@router.post("/api/apply")
def apply(req: ApplyRequest):
    """
    Generate config files, write them to disk, and activate them.

    Every real (non-dry-run) apply is armed with a connectivity-watchdog
    auto-revert: a detached timer (deploy/spud-commit.sh via systemd-run)
    is scheduled to restore the *previous* known-good state unless
    POST /api/apply/confirm cancels the timer within the window.

    Design decision: *every* apply is armed, rather than only ones
    classified as "connectivity-affecting" (WAN/VLAN/routes/firewall/VPN).
    Classifying changes correctly would require diffing state sections and
    guessing at every current and future feature's blast radius — a
    misclassification (treating something as "safe" when it wasn't) is
    exactly the kind of bug that could strand a remote admin permanently.
    Always-arm has no such failure mode: the worst case is an admin has to
    click "Keep changes" once after a config change that happened to be
    harmless. See PR description / issue #92 for this resolved open question.

    Rollback-target correctness (see #92 PR review): the state armed for
    revert is LAST_APPLIED_STATE_FILE — the config that was actually live
    *before* this apply — never the state being applied right now. Writing
    the new state into the rollback slot would make a revert re-apply the
    very change that may have broken connectivity, which defeats the whole
    feature. LAST_APPLIED_STATE_FILE only advances forward once this apply
    is confirmed (see apply_confirm()) or, for an apply that ends up
    unarmed, immediately (see _promote_to_last_applied()'s docstring).
    """
    state = load_state()
    generated = apply_core.generate_all(state)

    if req.dry_run:
        result = {"dry_run": True, "netplan": generated["netplan"],
                  "dnsmasq": generated["dnsmasq"], "iptables": generated["iptables"]}
        if generated["hostapd"]:
            result["hostapd"] = generated["hostapd"]
        if generated["syslog"]:
            result["syslog"] = generated["syslog"]
        if generated["snmp"]:
            result["snmp"] = _mask_snmp_preview(generated["snmp"], state)
        if generated["doh"]:
            result["doh"] = generated["doh"]
        if generated["bgp"]:
            result["bgp"] = generated["bgp"]
        return result

    try:
        results = apply_core.activate_all(state, sudo=True)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Record what was actually pushed live so /api/apply/status can tell
    # whether the current state still matches it — survives reboot/reload
    # since it's a file, not in-memory.
    _write_applied_snapshot(state)

    # The rollback target is whatever was live *before* this apply — read
    # it now, before any promotion happens below.
    previous_applied_state = None
    if LAST_APPLIED_STATE_FILE.exists():
        try:
            previous_applied_state = json.loads(LAST_APPLIED_STATE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            previous_applied_state = None

    if previous_applied_state is None:
        # Nothing known-good to fall back to (first-ever apply, or the
        # last-applied record was unreadable) — arming would only ever
        # "revert" to this same state, so don't arm at all. This apply's
        # own state becomes the new baseline immediately, since there is
        # no confirm step coming to promote it later.
        _promote_to_last_applied(state)
        ROLLBACK_STATE_FILE.unlink(missing_ok=True)
        ARM_STATUS_FILE.unlink(missing_ok=True)
        results.append(
            "ℹ No prior applied configuration on record — nothing to revert to, "
            "so this apply was not armed."
        )
        return {"ok": True, "steps": results, "armed": False}

    # Snapshot the *previous* known-good state as the revert target — not
    # the state we just activated. Service-user-writable, no sudo needed.
    ROLLBACK_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ROLLBACK_STATE_FILE.write_text(json.dumps(previous_applied_state, indent=2))

    # Arm the auto-revert watchdog.
    token = secrets.token_hex(8)
    arm_result = subprocess.run(
        ["sudo", str(SPUD_COMMIT_SCRIPT), "arm", str(ARM_WINDOW_SECONDS)],
        capture_output=True, text=True,
    )
    if arm_result.returncode != 0:
        # Arming failed — surface it but don't fail the whole apply; the
        # config IS live, the admin just won't get an auto-revert safety
        # net for this one. Better to say so than silently pretend it's armed.
        # Since nothing will ever confirm/revert this apply, promote it to
        # the baseline immediately rather than leaving LAST_APPLIED_STATE_FILE
        # stuck on the older state forever.
        _promote_to_last_applied(state)
        results.append(f"⚠ Could not arm auto-revert: {arm_result.stderr.strip()}")
        ROLLBACK_STATE_FILE.unlink(missing_ok=True)
        ARM_STATUS_FILE.unlink(missing_ok=True)
        return {"ok": True, "steps": results, "armed": False}

    ARM_STATUS_FILE.write_text(json.dumps({
        "token": token, "armed_at": time.time(), "window_seconds": ARM_WINDOW_SECONDS,
    }))

    return {
        "ok": True, "steps": results, "armed": True,
        "token": token, "window_seconds": ARM_WINDOW_SECONDS,
    }


@router.post("/api/apply/confirm")
def apply_confirm(req: ApplyConfirmRequest):
    """
    Cancel the pending auto-revert and prune the rollback snapshot — call
    this once the admin has confirmed the new config is working. The token
    must match the currently-armed apply, so a stale browser tab/session
    can't confirm (or accidentally let expire) a *different*, newer armed
    apply than the one it thinks it's looking at.
    """
    if not ARM_STATUS_FILE.exists():
        raise HTTPException(status_code=409, detail="Nothing is currently armed.")
    try:
        armed = json.loads(ARM_STATUS_FILE.read_text())
    except (OSError, ValueError):
        raise HTTPException(status_code=409, detail="Armed status is unreadable.")
    if req.token != armed.get("token"):
        raise HTTPException(status_code=409, detail="Token does not match the currently-armed apply.")

    result = subprocess.run(
        ["sudo", str(SPUD_COMMIT_SCRIPT), "confirm"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Failed to cancel the pending revert: {result.stderr.strip()}")

    # The now-confirmed config becomes the known-good baseline the *next*
    # apply's rollback snapshot is taken from — see apply()'s docstring.
    _promote_to_last_applied(load_state())

    ARM_STATUS_FILE.unlink(missing_ok=True)
    ROLLBACK_STATE_FILE.unlink(missing_ok=True)
    return {"ok": True, "confirmed": True}


@router.get("/api/apply/armed")
def apply_armed():
    """
    Whether an apply is currently armed (pending confirmation before
    auto-revert fires) — lets the UI restore the countdown banner after a
    page reload or a reconnect following a network change.
    """
    if not ARM_STATUS_FILE.exists():
        return {"armed": False}
    try:
        armed = json.loads(ARM_STATUS_FILE.read_text())
    except (OSError, ValueError):
        return {"armed": False}
    elapsed = time.time() - armed.get("armed_at", 0)
    remaining = max(0, armed.get("window_seconds", 0) - elapsed)
    return {
        "armed": True,
        "token": armed.get("token"),
        "window_seconds": armed.get("window_seconds"),
        "remaining_seconds": remaining,
    }


@router.get("/api/apply/status")
def apply_status():
    """
    Whether state.json has changes that haven't been pushed live via Apply.
    Compares the *generated* config output (not raw state) against the
    snapshot written by the last successful apply — so cosmetic state edits
    that don't change any emitted config correctly read as "nothing to
    apply". Split into the safe (sysctl) and unsafe (everything else)
    buckets (#184); "pending" is true if either differs, since a manual
    apply always activates both regardless of which one drifted.

    An old v1 snapshot (`{"hash": ...}`, written before #184 shipped) has no
    bucketing info — it's compared using only the unsafe-bucket hash, which
    is numerically identical to what this endpoint always computed
    pre-#184 (sysctls were never a separately hashed component). This makes
    an existing install's first status check after upgrading self-heal on
    the next manual Apply, which writes the v2 format.
    """
    state = load_state()
    current_safe_hash   = _safe_hash(state)
    current_unsafe_hash = _unsafe_hash(state)

    applied_safe_hash   = None
    applied_unsafe_hash = None
    if APPLIED_SNAPSHOT_FILE.exists():
        try:
            snap = json.loads(APPLIED_SNAPSHOT_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            snap = None
        if snap is not None:
            if "safe_hash" in snap or "unsafe_hash" in snap:
                applied_safe_hash   = snap.get("safe_hash")
                applied_unsafe_hash = snap.get("unsafe_hash")
            else:
                applied_unsafe_hash = snap.get("hash")

    pending = (applied_safe_hash != current_safe_hash) or (applied_unsafe_hash != current_unsafe_hash)

    return {
        "pending":              pending,
        "safe_hash":            current_safe_hash,
        "unsafe_hash":          current_unsafe_hash,
        "applied_safe_hash":    applied_safe_hash,
        "applied_unsafe_hash":  applied_unsafe_hash,
    }


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
        validated["fw_outbound"] = [OutboundRule(**r).model_dump() for r in data.get("fw_outbound", [])]
        if data.get("fw_outbound_default") in ("allow", "deny"):
            validated["fw_outbound_default"] = data["fw_outbound_default"]
        validated["port_forwards"] = [PortForward(**pf).model_dump() for pf in data.get("port_forwards", [])]

        if data.get("tailscale"):
            validated["tailscale"] = TailscaleConfig(**data["tailscale"]).model_dump()

        if data.get("wireless"):
            validated["wireless"] = WirelessConfig(**data["wireless"]).model_dump()

        if data.get("syslog"):
            validated["syslog"] = SyslogConfig(**data["syslog"]).model_dump()

        if data.get("snmp"):
            validated["snmp"] = SnmpConfig(**data["snmp"]).model_dump()

        if data.get("bgp"):
            validated["bgp"] = BgpConfig(**data["bgp"]).model_dump()

        if data.get("wireguard"):
            validated["wireguard"] = WireguardConfig(**data["wireguard"]).model_dump()

        if data.get("nebula"):
            validated["nebula"] = NebulaConfig(**data["nebula"]).model_dump()

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
        "fw_outbound":  len(validated["fw_outbound"]),
        "port_forwards": len(validated["port_forwards"]),
        "bgp":          bool(validated["bgp"]["enabled"]) if validated.get("bgp") else False,
        "wireguard":    bool(validated["wireguard"]["enabled"]) if validated.get("wireguard") else False,
        "nebula":       bool(validated["nebula"]["enabled"]) if validated.get("nebula") else False,
    }
