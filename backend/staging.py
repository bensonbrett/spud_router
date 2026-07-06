# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Transactional staging pipeline for MCP and programmatic configuration.

Implements a staging buffer between mutation requests and live state, with
explicit validate and commit phases, and an auto-revert safety timer on commit.

State machine:
  IDLE → begin → STAGING → op → STAGING → validate → VALIDATED → commit → APPLIED → confirm → CONFIRMED
                                ↓                          ↓                    ↓
                              discard                   discard              timer expires
                                ↓                          ↓                    ↓
                              IDLE                      IDLE                 REVERTED → IDLE
"""
import json
import os
import secrets
import stat
import time
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from . import apply_core
from .models import (
    DnsEntry, InboundRule, InterVlanRule, NebulaConfig, OutboundRule,
    PortForward, RouterConfig, SnmpConfig, StaticRoute, SyslogConfig,
    TailscaleConfig, VlanConfig, WirelessConfig, WirelessSsid,
    WireguardConfig, WireguardPeerCreateRequest,
)
from .state import (
    ARM_STATUS_FILE, LAST_APPLIED_STATE_FILE, ROLLBACK_STATE_FILE,
    SPUD_CONF, STAGING_FILE, empty_state, load_state, save_state,
)
from .update import SPUD_COMMIT_SCRIPT
from .vpn_coexistence import validate_single_route_all

STAGING_FILE = SPUD_CONF / "mcp-staging.json"

CONFIRM_WINDOW_SECONDS = 120


class StagingError(Exception):
    """Raised when a staging operation fails validation or constraints."""
    pass


class ValidationResult:
    def __init__(self):
        self.errors: list[dict] = []
        self.warnings: list[str] = []

    def add_error(self, section: str, message: str):
        self.errors.append({"section": section, "message": message})

    def add_warning(self, message: str):
        self.warnings.append(message)

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _load_staging() -> dict | None:
    if not STAGING_FILE.exists():
        return None
    try:
        return json.loads(STAGING_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_staging(data: dict) -> None:
    SPUD_CONF.mkdir(parents=True, exist_ok=True)
    tmp = STAGING_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.rename(STAGING_FILE)
    os.chmod(STAGING_FILE, stat.S_IRUSR | stat.S_IWUSR)


def _get_staging_state(staging: dict) -> str:
    return staging.get("_meta", {}).get("state", "idle")


def _subnets_overlap(v1: dict, v2: dict) -> bool:
    """Check if two VLANs have overlapping subnets."""
    import ipaddress
    try:
        net1 = ipaddress.IPv4Network(
            f"{v1['ip_address']}/{v1['prefix_len']}", strict=False
        )
        net2 = ipaddress.IPv4Network(
            f"{v2['ip_address']}/{v2['prefix_len']}", strict=False
        )
        return net1.overlaps(net2)
    except (ValueError, KeyError):
        return False


def _check_connectivity_impact(staging: dict, live: dict, result: ValidationResult):
    """Detect changes that could affect admin reachability."""
    staged_router = staging.get("router", {})
    live_router = live.get("router", {})

    if staged_router.get("mgmt_ip") != live_router.get("mgmt_ip"):
        result.add_warning(
            "Management interface IP changed — ensure you can still reach the router"
        )


# ── Operation handlers ─────────────────────────────────────────────────────────

def _op_set_router(staging: dict, data: dict) -> dict:
    validated = RouterConfig(**data).model_dump()
    staging["router"] = validated
    return staging


def _op_add_vlan(staging: dict, data: dict) -> dict:
    validated = VlanConfig(**data).model_dump()
    vlans = staging.get("vlans", [])
    if any(
        v["vlan_id"] == validated["vlan_id"] and v["interface"] == validated["interface"]
        for v in vlans
    ):
        raise StagingError(f"VLAN {validated['vlan_id']} on {validated['interface']} already exists")
    vlans.append(validated)
    staging["vlans"] = vlans
    return staging


def _op_update_vlan(staging: dict, data: dict) -> dict:
    validated = VlanConfig(**data).model_dump()
    vlans = staging.get("vlans", [])
    idx = next(
        (i for i, v in enumerate(vlans) if v["vlan_id"] == validated["vlan_id"]),
        None
    )
    if idx is None:
        raise StagingError(f"VLAN {validated['vlan_id']} not found")
    if any(
        i != idx
        and v["vlan_id"] == validated["vlan_id"]
        and v["interface"] == validated["interface"]
        for i, v in enumerate(vlans)
    ):
        raise StagingError(
            f"VLAN {validated['vlan_id']} on {validated['interface']} already exists"
        )
    vlans[idx] = validated
    staging["vlans"] = vlans
    return staging


def _op_delete_vlan(staging: dict, data: dict) -> dict:
    vlan_id = data.get("vlan_id")
    if not isinstance(vlan_id, int):
        raise StagingError("vlan_id must be an integer")
    before = len(staging.get("vlans", []))
    staging["vlans"] = [
        v for v in staging.get("vlans", []) if v["vlan_id"] != vlan_id
    ]
    if len(staging["vlans"]) == before:
        raise StagingError(f"VLAN {vlan_id} not found")
    return staging


def _op_add_dns(staging: dict, data: dict) -> dict:
    validated = DnsEntry(**data).model_dump()
    entries = staging.get("dns_entries", [])
    if any(e["hostname"] == validated["hostname"] for e in entries):
        raise StagingError(f"DNS entry for {validated['hostname']} already exists")
    entries.append(validated)
    staging["dns_entries"] = entries
    return staging


def _op_delete_dns(staging: dict, data: dict) -> dict:
    hostname = data.get("hostname")
    if not hostname:
        raise StagingError("hostname is required")
    before = len(staging.get("dns_entries", []))
    staging["dns_entries"] = [
        e for e in staging.get("dns_entries", []) if e["hostname"] != hostname
    ]
    if len(staging["dns_entries"]) == before:
        raise StagingError(f"DNS entry for {hostname} not found")
    return staging


def _op_add_route(staging: dict, data: dict) -> dict:
    validated = StaticRoute(**data).model_dump()
    routes = staging.get("static_routes", [])
    if any(r["destination"] == validated["destination"] for r in routes):
        raise StagingError(f"Route to {validated['destination']} already exists")
    routes.append(validated)
    staging["static_routes"] = routes
    return staging


def _op_delete_route(staging: dict, data: dict) -> dict:
    destination = data.get("destination")
    if not destination:
        raise StagingError("destination is required")
    before = len(staging.get("static_routes", []))
    staging["static_routes"] = [
        r for r in staging.get("static_routes", []) if r["destination"] != destination
    ]
    if len(staging["static_routes"]) == before:
        raise StagingError(f"Route to {destination} not found")
    return staging


def _op_add_fw_rule(
    staging: dict, data: dict, section: str, model_cls: type
) -> dict:
    validated = model_cls(**data).model_dump()
    rules = staging.get(section, [])
    rules.append(validated)
    staging[section] = rules
    return staging


def _op_delete_fw_rule(staging: dict, data: dict, section: str) -> dict:
    rule_id = data.get("id")
    if not rule_id:
        raise StagingError("id is required")
    before = len(staging.get(section, []))
    staging[section] = [r for r in staging.get(section, []) if r.get("id") != rule_id]
    if len(staging[section]) == before:
        raise StagingError(f"Rule {rule_id} not found in {section}")
    return staging


def _op_add_port_forward(staging: dict, data: dict) -> dict:
    validated = PortForward(**data).model_dump()
    fwd = staging.get("port_forwards", [])
    fwd.append(validated)
    staging["port_forwards"] = fwd
    return staging


def _op_update_port_forward(staging: dict, data: dict) -> dict:
    validated = PortForward(**data).model_dump()
    fwd = staging.get("port_forwards", [])
    idx = next(
        (i for i, f in enumerate(fwd) if f.get("id") == validated.get("id")),
        None
    )
    if idx is None:
        raise StagingError(f"Port forward {validated.get('id')} not found")
    fwd[idx] = validated
    staging["port_forwards"] = fwd
    return staging


def _op_delete_port_forward(staging: dict, data: dict) -> dict:
    forward_id = data.get("forward_id") or data.get("id")
    if not forward_id:
        raise StagingError("forward_id or id is required")
    before = len(staging.get("port_forwards", []))
    staging["port_forwards"] = [
        f for f in staging.get("port_forwards", []) if f.get("id") != forward_id
    ]
    if len(staging["port_forwards"]) == before:
        raise StagingError(f"Port forward {forward_id} not found")
    return staging


def _op_set_tailscale(staging: dict, data: dict) -> dict:
    validated = TailscaleConfig(**data).model_dump()
    staging["tailscale"] = validated
    return staging


def _op_set_wireguard(staging: dict, data: dict) -> dict:
    current = staging.get("wireguard", {})
    validated = WireguardConfig(**data).model_dump()
    # Preserve private key sentinel if not provided
    if not validated.get("private_key") and current.get("private_key"):
        validated["private_key"] = current["private_key"]
    staging["wireguard"] = validated
    return staging


def _op_add_wg_peer(staging: dict, data: dict) -> dict:
    validated = WireguardPeerCreateRequest(**data).model_dump()
    wg = staging.get("wireguard", {})
    peers = wg.get("peers", [])
    peers.append(validated)
    wg["peers"] = peers
    staging["wireguard"] = wg
    return staging


def _op_delete_wg_peer(staging: dict, data: dict) -> dict:
    peer_id = data.get("peer_id")
    if not peer_id:
        raise StagingError("peer_id is required")
    wg = staging.get("wireguard", {})
    peers = wg.get("peers", [])
    before = len(peers)
    peers = [p for p in peers if p.get("id") != peer_id]
    if len(peers) == before:
        raise StagingError(f"WireGuard peer {peer_id} not found")
    wg["peers"] = peers
    staging["wireguard"] = wg
    return staging


def _op_set_nebula(staging: dict, data: dict) -> dict:
    current = staging.get("nebula", {})
    validated = NebulaConfig(**data).model_dump()
    # Preserve credentials
    validated["cert_pem"] = current.get("cert_pem", "")
    validated["key_pem"] = current.get("key_pem", "")
    validated["ca_pem"] = current.get("ca_pem", "")
    staging["nebula"] = validated
    return staging


def _op_set_wireless(staging: dict, data: dict) -> dict:
    validated = WirelessConfig(**data).model_dump()
    staging["wireless"] = validated
    return staging


def _op_add_ssid(staging: dict, data: dict) -> dict:
    validated = WirelessSsid(**data).model_dump()
    wireless = staging.get("wireless", {})
    ssids = wireless.get("ssids", [])
    ssids.append(validated)
    wireless["ssids"] = ssids
    staging["wireless"] = wireless
    return staging


def _op_delete_ssid(staging: dict, data: dict) -> dict:
    ssid_id = data.get("ssid_id") or data.get("id")
    if not ssid_id:
        raise StagingError("ssid_id or id is required")
    wireless = staging.get("wireless", {})
    ssids = wireless.get("ssids", [])
    before = len(ssids)
    ssids = [s for s in ssids if s.get("id") != ssid_id]
    if len(ssids) == before:
        raise StagingError(f"SSID {ssid_id} not found")
    wireless["ssids"] = ssids
    staging["wireless"] = wireless
    return staging


def _op_set_syslog(staging: dict, data: dict) -> dict:
    validated = SyslogConfig(**data).model_dump()
    staging["syslog"] = validated
    return staging


def _op_set_snmp(staging: dict, data: dict) -> dict:
    current = staging.get("snmp", {})
    validated = SnmpConfig(**data).model_dump()
    # Preserve community strings
    if current.get("community_ro"):
        validated["community_ro"] = current["community_ro"]
    if current.get("community_rw"):
        validated["community_rw"] = current["community_rw"]
    staging["snmp"] = validated
    return staging


def _op_import_state(staging: dict, data: dict) -> dict:
    for key, value in data.items():
        if key != "_meta":
            staging[key] = value
    return staging


OP_HANDLERS: dict[str, Callable[[dict, dict], dict]] = {
    "set_router": _op_set_router,
    "add_vlan": _op_add_vlan,
    "update_vlan": _op_update_vlan,
    "delete_vlan": _op_delete_vlan,
    "add_dns": _op_add_dns,
    "delete_dns": _op_delete_dns,
    "add_route": _op_add_route,
    "delete_route": _op_delete_route,
    "add_fw_inbound": lambda s, d: _op_add_fw_rule(s, d, "fw_inbound", InboundRule),
    "delete_fw_inbound": lambda s, d: _op_delete_fw_rule(s, d, "fw_inbound"),
    "add_fw_intervlan": lambda s, d: _op_add_fw_rule(s, d, "fw_intervlan", InterVlanRule),
    "delete_fw_intervlan": lambda s, d: _op_delete_fw_rule(s, d, "fw_intervlan"),
    "add_fw_outbound": lambda s, d: _op_add_fw_rule(s, d, "fw_outbound", OutboundRule),
    "delete_fw_outbound": lambda s, d: _op_delete_fw_rule(s, d, "fw_outbound"),
    "add_port_forward": _op_add_port_forward,
    "update_port_forward": _op_update_port_forward,
    "delete_port_forward": _op_delete_port_forward,
    "set_tailscale": _op_set_tailscale,
    "set_wireguard": _op_set_wireguard,
    "add_wg_peer": _op_add_wg_peer,
    "delete_wg_peer": _op_delete_wg_peer,
    "set_nebula": _op_set_nebula,
    "set_wireless": _op_set_wireless,
    "add_ssid": _op_add_ssid,
    "delete_ssid": _op_delete_ssid,
    "set_syslog": _op_set_syslog,
    "set_snmp": _op_set_snmp,
    "import_state": _op_import_state,
}


def apply_operation(staging: dict, op: str, data: dict) -> dict:
    """Apply a single operation to the staging buffer."""
    handler = OP_HANDLERS.get(op)
    if handler is None:
        raise StagingError(f"Unknown operation: {op}")
    try:
        return handler(staging, data)
    except ValidationError as e:
        errors = e.errors()
        if errors:
            field = ".".join(str(loc) for loc in errors[0]["loc"])
            msg = errors[0]["msg"]
            raise StagingError(f"Validation error in {field}: {msg}")
        raise StagingError(f"Validation error: {e}")
    except ValueError as e:
        raise StagingError(str(e))


def validate_staging(staging: dict) -> ValidationResult:
    """Run comprehensive validation against the staged state."""
    result = ValidationResult()
    live = load_state()

    # Phase 1: Schema validation
    try:
        if staging.get("router"):
            RouterConfig(**staging["router"])
    except ValidationError as e:
        result.add_error("router", f"Schema validation failed: {e.errors()[0]['msg']}")

    for i, vlan in enumerate(staging.get("vlans", [])):
        try:
            VlanConfig(**vlan)
        except ValidationError as e:
            result.add_error("vlans", f"VLAN at index {i}: {e.errors()[0]['msg']}")

    for i, route in enumerate(staging.get("static_routes", [])):
        try:
            StaticRoute(**route)
        except ValidationError as e:
            result.add_error("static_routes", f"Route at index {i}: {e.errors()[0]['msg']}")

    for i, entry in enumerate(staging.get("dns_entries", [])):
        try:
            DnsEntry(**entry)
        except ValidationError as e:
            result.add_error("dns_entries", f"DNS entry at index {i}: {e.errors()[0]['msg']}")

    for i, rule in enumerate(staging.get("fw_inbound", [])):
        try:
            InboundRule(**rule)
        except ValidationError as e:
            result.add_error("fw_inbound", f"Rule at index {i}: {e.errors()[0]['msg']}")

    for i, rule in enumerate(staging.get("fw_intervlan", [])):
        try:
            InterVlanRule(**rule)
        except ValidationError as e:
            result.add_error("fw_intervlan", f"Rule at index {i}: {e.errors()[0]['msg']}")

    for i, rule in enumerate(staging.get("fw_outbound", [])):
        try:
            OutboundRule(**rule)
        except ValidationError as e:
            result.add_error("fw_outbound", f"Rule at index {i}: {e.errors()[0]['msg']}")

    for i, pf in enumerate(staging.get("port_forwards", [])):
        try:
            PortForward(**pf)
        except ValidationError as e:
            result.add_error("port_forwards", f"Port forward at index {i}: {e.errors()[0]['msg']}")

    if staging.get("tailscale"):
        try:
            TailscaleConfig(**staging["tailscale"])
        except ValidationError as e:
            result.add_error("tailscale", f"Schema validation failed: {e.errors()[0]['msg']}")

    if staging.get("wireguard"):
        try:
            WireguardConfig(**staging["wireguard"])
        except ValidationError as e:
            result.add_error("wireguard", f"Schema validation failed: {e.errors()[0]['msg']}")

    if staging.get("nebula"):
        try:
            NebulaConfig(**staging["nebula"])
        except ValidationError as e:
            result.add_error("nebula", f"Schema validation failed: {e.errors()[0]['msg']}")

    if staging.get("wireless"):
        try:
            WirelessConfig(**staging["wireless"])
        except ValidationError as e:
            result.add_error("wireless", f"Schema validation failed: {e.errors()[0]['msg']}")

    if staging.get("syslog"):
        try:
            SyslogConfig(**staging["syslog"])
        except ValidationError as e:
            result.add_error("syslog", f"Schema validation failed: {e.errors()[0]['msg']}")

    # Phase 2: Cross-section consistency
    try:
        validate_single_route_all(staging)
    except ValueError as e:
        result.add_error("vpn", str(e))

    # Subnet overlap check
    vlans = staging.get("vlans", [])
    for i, v1 in enumerate(vlans):
        for v2 in vlans[i+1:]:
            if _subnets_overlap(v1, v2):
                result.add_error(
                    "vlans",
                    f"VLAN {v1['vlan_id']} subnet overlaps with VLAN {v2['vlan_id']}"
                )

    # Phase 3: Config generation dry-run
    generated = None
    try:
        generated = apply_core.generate_all(staging)
    except Exception as e:
        result.add_error("generation", f"Config generation failed: {e}")

    # Phase 4: Connectivity impact analysis (warnings)
    _check_connectivity_impact(staging, live, result)

    return result


def commit_staging(confirm_window: int = CONFIRM_WINDOW_SECONDS) -> dict:
    """Atomically promote staged state to live and activate configs."""
    import subprocess

    staging_data = _load_staging()
    if not staging_data:
        raise StagingError("No staging transaction is active")

    meta = staging_data.get("_meta", {})
    validation = meta.get("validation")
    if not validation or not validation.get("valid"):
        raise StagingError("Staged state has not been validated. Call POST /api/staging/validate first.")

    staged_state = {k: v for k, v in staging_data.items() if k != "_meta"}

    # Step 1: Snapshot rollback target
    rollback_target = None
    if LAST_APPLIED_STATE_FILE.exists():
        try:
            rollback_target = json.loads(LAST_APPLIED_STATE_FILE.read_text())
            ROLLBACK_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            ROLLBACK_STATE_FILE.write_text(json.dumps(rollback_target, indent=2))
            os.chmod(ROLLBACK_STATE_FILE, stat.S_IRUSR | stat.S_IWUSR)
        except Exception as e:
            raise StagingError(f"Failed to snapshot rollback target: {e}")

    # Step 2: Atomic promotion
    try:
        save_state(staged_state)
    except Exception as e:
        _attempt_rollback(rollback_target)
        raise StagingError(f"Failed to promote staged state: {e}")

    # Step 3: Activate configs
    steps = []
    try:
        steps = apply_core.activate_all(staged_state, sudo=True)
    except RuntimeError as e:
        _attempt_rollback(rollback_target)
        raise StagingError(f"Config activation failed: {e}. Rolled back to previous state.")

    # Step 4: Arm auto-revert
    token = secrets.token_hex(8)
    arm_result = subprocess.run(
        ["sudo", str(SPUD_COMMIT_SCRIPT), "arm", str(confirm_window)],
        capture_output=True, text=True,
    )
    if arm_result.returncode != 0:
        steps.append(f"Could not arm auto-revert: {arm_result.stderr.strip()}")
        _promote_to_last_applied(staged_state)
        return {
            "ok": True,
            "armed": False,
            "steps": steps,
        }

    # Step 5: Write arm status
    ARM_STATUS_FILE.write_text(json.dumps({
        "token": token,
        "armed_at": time.time(),
        "window_seconds": confirm_window,
    }))
    os.chmod(ARM_STATUS_FILE, stat.S_IRUSR | stat.S_IWUSR)

    # Step 6: Clear staging buffer
    STAGING_FILE.unlink()

    return {
        "ok": True,
        "armed": True,
        "token": token,
        "window_seconds": confirm_window,
        "armed_at": time.time(),
        "expires_at": time.time() + confirm_window,
        "steps": steps,
    }


def _attempt_rollback(rollback_target: dict | None):
    if rollback_target is None:
        return
    try:
        save_state(rollback_target)
        apply_core.activate_all(rollback_target, sudo=True)
    except Exception:
        pass


def _promote_to_last_applied(state: dict):
    LAST_APPLIED_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_APPLIED_STATE_FILE.write_text(json.dumps(state, indent=2))
    os.chmod(LAST_APPLIED_STATE_FILE, stat.S_IRUSR | stat.S_IWUSR)


def confirm_commit(token: str) -> bool:
    """Confirm a pending commit, cancelling the auto-revert watchdog."""
    import subprocess

    if not ARM_STATUS_FILE.exists():
        return False
    try:
        armed = json.loads(ARM_STATUS_FILE.read_text())
    except (OSError, ValueError):
        return False
    if token != armed.get("token"):
        return False

    result = subprocess.run(
        ["sudo", str(SPUD_COMMIT_SCRIPT), "confirm"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False

    _promote_to_last_applied(load_state())
    ARM_STATUS_FILE.unlink(missing_ok=True)
    ROLLBACK_STATE_FILE.unlink(missing_ok=True)
    return True
