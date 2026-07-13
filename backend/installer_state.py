# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
State-building helpers for install.sh's NIC detection/assignment flow
(issue #195).

Pure functions — no filesystem or subprocess I/O — so the single-NIC
default, single-NIC customize, and multi-NIC physical-topology paths are
all unit testable (see tests/test_installer_state.py). install.sh shells
out to this module's CLI (`python3 installer_state.py <subcommand> ...`)
to render the state.json body and to validate/re-prompt on interactive
input; the shell script itself only handles NIC detection, prompting, and
writing the result to disk.

Only the standard library is used (no fastapi/pydantic) so this module
runs with the system python3, before the app's venv even exists.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import sys


# ── Validation ────────────────────────────────────────────────────────────

def validate_vlan_id(v: str) -> int:
    try:
        n = int(v)
    except ValueError:
        raise ValueError(f"VLAN ID must be an integer between 1 and 4094 (got {v!r})")
    if not 1 <= n <= 4094:
        raise ValueError(f"VLAN ID must be between 1 and 4094 (got {v})")
    return n


def validate_ip(v: str) -> str:
    try:
        ipaddress.IPv4Address(v)
    except ValueError:
        raise ValueError(f"Invalid IPv4 address: {v}")
    return v


def validate_cidr(v: str) -> tuple[str, int]:
    """Return (ip, prefix_len) for a valid IPv4 host/CIDR like '192.168.10.1/24'."""
    if "/" not in v:
        raise ValueError(f"Expected an IPv4 address with a prefix, e.g. 192.168.10.1/24 (got {v!r})")
    try:
        iface = ipaddress.ip_interface(v)
    except ValueError:
        raise ValueError(f"Invalid IPv4 address/CIDR: {v}")
    if not isinstance(iface, ipaddress.IPv4Interface):
        raise ValueError(f"{v} is not an IPv4 address/CIDR")
    prefix = iface.network.prefixlen
    if not 1 <= prefix <= 30:
        raise ValueError(f"Prefix length must be between 1 and 30 (got /{prefix})")
    return str(iface.ip), prefix


def validate_dhcp_range(cidr: str, start: str, end: str) -> tuple[str, str]:
    ip, prefix = validate_cidr(cidr)
    network = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    try:
        s = ipaddress.IPv4Address(start)
        e = ipaddress.IPv4Address(end)
    except ValueError:
        raise ValueError(f"DHCP start/end must be valid IPv4 addresses (got {start!r}, {end!r})")
    if s not in network or e not in network:
        raise ValueError(f"DHCP range {start}-{end} is not within {network}")
    if int(s) > int(e):
        raise ValueError(f"DHCP range start {start} must not be after end {end}")
    return start, end


def suggest_dhcp_range(cidr: str, end_offset: int = 200) -> tuple[str, str]:
    """Suggest a DHCP start/end within cidr's subnet, for use as an
    interactive-prompt default. Tries the conventional '.100'/'.<end_offset>'
    host suffixes first; falls back to a proportional slice of the usable
    host range for subnets too small to fit that convention (e.g. /28)."""
    ip, prefix = validate_cidr(cidr)
    network = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    gateway = ipaddress.IPv4Address(ip)
    usable = [h for h in network.hosts() if h != gateway]
    if len(usable) < 2:
        raise ValueError(f"{cidr} is too small to hold a DHCP range")

    base = int(network.network_address)
    candidate_start = ipaddress.IPv4Address(base + 100)
    candidate_end = ipaddress.IPv4Address(base + end_offset)
    if (
        candidate_start in usable
        and candidate_end in usable
        and int(candidate_start) < int(candidate_end)
    ):
        return str(candidate_start), str(candidate_end)

    # Fallback for small subnets: middle half of the usable host range.
    quarter = max(1, len(usable) // 4)
    lo = usable[quarter - 1]
    hi = usable[-quarter]
    if int(lo) >= int(hi):
        lo, hi = usable[0], usable[-1]
    return str(lo), str(hi)


# ── State builders ────────────────────────────────────────────────────────

def _tailscale_block() -> dict:
    return {"enabled": False, "advertise_routes": [], "exit_node": False, "accept_routes": True}


def default_single_nic_state(trunk_if: str) -> dict:
    """The exact router-on-a-stick template install.sh has always written —
    byte-for-byte (see tests/test_installer_state.py's golden-state test).
    Do not change key order or values here without re-checking that test;
    it's the regression anchor for the single-NIC install path."""
    wan_vlan = f"{trunk_if}.2"
    return {
        "vlans": [
            {
                "vlan_id": 2, "name": "WAN", "interface": trunk_if,
                "ip_address": "", "prefix_len": 0,
                "dhcp_enabled": False, "dhcp_start": "", "dhcp_end": "",
                "dhcp_lease": "12h", "isolate": False,
            },
            {
                "vlan_id": 10, "name": "LAN", "interface": trunk_if,
                "ip_address": "192.168.10.1", "prefix_len": 24,
                "dhcp_enabled": True, "dhcp_start": "192.168.10.100", "dhcp_end": "192.168.10.200",
                "dhcp_lease": "12h", "isolate": False,
            },
        ],
        "router": {
            "wan_interface": wan_vlan, "wan_mode": "dhcp", "wan_dns_mode": "auto",
            "wan_dns": "1.1.1.1", "wan_dns_alt": "8.8.8.8", "hostname": "spud-router",
            "mgmt_enabled": True, "mgmt_interface": trunk_if, "mgmt_ip": "192.168.1.1",
            "mgmt_prefix": 24, "mgmt_dhcp_start": "192.168.1.100", "mgmt_dhcp_end": "192.168.1.150",
            "mgmt_dhcp_lease": "12h",
        },
        "static_routes": [],
        "dns_entries": [],
        "tailscale": _tailscale_block(),
        "fw_inbound": [],
        "fw_intervlan": [],
    }


def custom_single_nic_state(
    trunk_if: str,
    lan_vlan_id: int,
    lan_cidr: str,
    lan_dhcp_start: str,
    lan_dhcp_end: str,
    wan_vlan_id: int,
    wan_mode: str = "dhcp",
    wan_cidr: str | None = None,
    wan_gateway: str | None = None,
    mgmt_cidr: str = "192.168.1.1/24",
    mgmt_dhcp_start: str = "192.168.1.100",
    mgmt_dhcp_end: str = "192.168.1.150",
) -> dict:
    """Single-NIC path with customized VLAN IDs / IP plans (issue #195 §2a).
    Still router-on-a-stick (WAN + LAN VLANs + untagged mgmt on one trunk
    port) — only the specific IDs/subnets differ from the defaults."""
    if wan_mode not in ("dhcp", "static"):
        raise ValueError("wan_mode must be 'dhcp' or 'static'")
    if lan_vlan_id == wan_vlan_id:
        raise ValueError("LAN VLAN ID and WAN VLAN ID must differ")

    lan_ip, lan_prefix = validate_cidr(lan_cidr)
    validate_dhcp_range(lan_cidr, lan_dhcp_start, lan_dhcp_end)
    mgmt_ip, mgmt_prefix = validate_cidr(mgmt_cidr)
    validate_dhcp_range(mgmt_cidr, mgmt_dhcp_start, mgmt_dhcp_end)

    router: dict = {
        "wan_interface": f"{trunk_if}.{wan_vlan_id}",
        "wan_mode": wan_mode,
        "wan_dns_mode": "auto",
        "wan_dns": "1.1.1.1",
        "wan_dns_alt": "8.8.8.8",
        "hostname": "spud-router",
        "mgmt_enabled": True,
        "mgmt_interface": trunk_if,
        "mgmt_ip": mgmt_ip,
        "mgmt_prefix": mgmt_prefix,
        "mgmt_dhcp_start": mgmt_dhcp_start,
        "mgmt_dhcp_end": mgmt_dhcp_end,
        "mgmt_dhcp_lease": "12h",
    }
    if wan_mode == "static":
        if not wan_cidr or not wan_gateway:
            raise ValueError("wan_cidr and wan_gateway are required when wan_mode is 'static'")
        wan_ip, wan_prefix = validate_cidr(wan_cidr)
        validate_ip(wan_gateway)
        router["wan_ip"] = wan_ip
        router["wan_prefix"] = wan_prefix
        router["wan_gateway"] = wan_gateway

    return {
        "vlans": [
            {
                "vlan_id": wan_vlan_id, "name": "WAN", "interface": trunk_if,
                "ip_address": "", "prefix_len": 0,
                "dhcp_enabled": False, "dhcp_start": "", "dhcp_end": "",
                "dhcp_lease": "12h", "isolate": False,
            },
            {
                "vlan_id": lan_vlan_id, "name": "LAN", "interface": trunk_if,
                "ip_address": lan_ip, "prefix_len": lan_prefix,
                "dhcp_enabled": True, "dhcp_start": lan_dhcp_start, "dhcp_end": lan_dhcp_end,
                "dhcp_lease": "12h", "isolate": False,
            },
        ],
        "router": router,
        "static_routes": [],
        "dns_entries": [],
        "tailscale": _tailscale_block(),
        "fw_inbound": [],
        "fw_intervlan": [],
    }


def multi_nic_state(
    wan_if: str,
    lan_if: str,
    mgmt_if: str | None = None,
    lan_cidr: str = "192.168.10.1/24",
    lan_dhcp_start: str = "192.168.10.100",
    lan_dhcp_end: str = "192.168.10.200",
    mgmt_cidr: str = "192.168.1.1/24",
    mgmt_dhcp_start: str = "192.168.1.100",
    mgmt_dhcp_end: str = "192.168.1.150",
) -> dict:
    """Multi-NIC physical-port topology (issue #195 §4): WAN and LAN live on
    separate physical interfaces with no VLAN tag. LAN is modeled as a
    VlanConfig entry with vlan_id=0 — the "untagged physical interface"
    sentinel the generators treat like a bare ethernets stanza rather than
    an 802.1Q subinterface (see backend/models.py, generators/netplan.py).

    mgmt_if is optional: when omitted (or equal to lan_if), management is
    "folded into LAN" (mgmt_enabled=False) — LAN is already a bare physical
    port, so there's no separate untagged-trunk-port rationale for a
    distinct mgmt subnet the way there is in the single-NIC/VLAN-trunk case.
    """
    if wan_if == lan_if:
        raise ValueError("WAN and LAN must be different physical interfaces")

    lan_ip, lan_prefix = validate_cidr(lan_cidr)
    validate_dhcp_range(lan_cidr, lan_dhcp_start, lan_dhcp_end)

    use_separate_mgmt = bool(mgmt_if) and mgmt_if != lan_if
    if use_separate_mgmt and mgmt_if == wan_if:
        raise ValueError("Management interface must differ from WAN")

    router: dict = {
        "wan_interface": wan_if,
        "wan_mode": "dhcp",
        "wan_dns_mode": "auto",
        "wan_dns": "1.1.1.1",
        "wan_dns_alt": "8.8.8.8",
        "hostname": "spud-router",
        "mgmt_enabled": use_separate_mgmt,
        "mgmt_interface": mgmt_if if use_separate_mgmt else lan_if,
        "mgmt_ip": "192.168.1.1",
        "mgmt_prefix": 24,
        "mgmt_dhcp_start": "192.168.1.100",
        "mgmt_dhcp_end": "192.168.1.150",
        "mgmt_dhcp_lease": "12h",
    }
    if use_separate_mgmt:
        mgmt_ip, mgmt_prefix = validate_cidr(mgmt_cidr)
        validate_dhcp_range(mgmt_cidr, mgmt_dhcp_start, mgmt_dhcp_end)
        router.update({
            "mgmt_ip": mgmt_ip,
            "mgmt_prefix": mgmt_prefix,
            "mgmt_dhcp_start": mgmt_dhcp_start,
            "mgmt_dhcp_end": mgmt_dhcp_end,
        })

    return {
        "vlans": [
            {
                "vlan_id": 0, "name": "LAN", "interface": lan_if,
                "ip_address": lan_ip, "prefix_len": lan_prefix,
                "dhcp_enabled": True, "dhcp_start": lan_dhcp_start, "dhcp_end": lan_dhcp_end,
                "dhcp_lease": "12h", "isolate": False,
            },
        ],
        "router": router,
        "static_routes": [],
        "dns_entries": [],
        "tailscale": _tailscale_block(),
        "fw_inbound": [],
        "fw_intervlan": [],
    }


def render(state: dict) -> str:
    """Compact JSON, matching install.sh's historical heredoc formatting
    (no whitespace) byte-for-byte for the default template."""
    return json.dumps(state, separators=(",", ":"))


# ── CLI ───────────────────────────────────────────────────────────────────

def _cmd_single_default(args: argparse.Namespace) -> int:
    print(render(default_single_nic_state(args.trunk_if)))
    return 0


def _cmd_single_custom(args: argparse.Namespace) -> int:
    state = custom_single_nic_state(
        trunk_if=args.trunk_if,
        lan_vlan_id=validate_vlan_id(str(args.lan_vlan_id)),
        lan_cidr=args.lan_cidr,
        lan_dhcp_start=args.lan_dhcp_start,
        lan_dhcp_end=args.lan_dhcp_end,
        wan_vlan_id=validate_vlan_id(str(args.wan_vlan_id)),
        wan_mode=args.wan_mode,
        wan_cidr=args.wan_cidr,
        wan_gateway=args.wan_gateway,
        mgmt_cidr=args.mgmt_cidr,
        mgmt_dhcp_start=args.mgmt_dhcp_start,
        mgmt_dhcp_end=args.mgmt_dhcp_end,
    )
    print(render(state))
    return 0


def _cmd_multi(args: argparse.Namespace) -> int:
    state = multi_nic_state(
        wan_if=args.wan_if,
        lan_if=args.lan_if,
        mgmt_if=args.mgmt_if,
        lan_cidr=args.lan_cidr,
        lan_dhcp_start=args.lan_dhcp_start,
        lan_dhcp_end=args.lan_dhcp_end,
        mgmt_cidr=args.mgmt_cidr,
        mgmt_dhcp_start=args.mgmt_dhcp_start,
        mgmt_dhcp_end=args.mgmt_dhcp_end,
    )
    print(render(state))
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        if args.kind == "vlan-id":
            print(validate_vlan_id(args.values[0]))
        elif args.kind == "ip":
            print(validate_ip(args.values[0]))
        elif args.kind == "cidr":
            ip, prefix = validate_cidr(args.values[0])
            print(f"{ip}/{prefix}")
        elif args.kind == "dhcp-range":
            cidr, start, end = args.values
            validate_dhcp_range(cidr, start, end)
            print(f"{start} {end}")
        else:
            print(f"Unknown validate kind: {args.kind}", file=sys.stderr)
            return 2
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def _cmd_suggest_dhcp_range(args: argparse.Namespace) -> int:
    try:
        start, end = suggest_dhcp_range(args.cidr, args.end_offset)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"{start} {end}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("single-default", help="Default router-on-a-stick state.json")
    p.add_argument("--trunk-if", required=True)
    p.set_defaults(func=_cmd_single_default)

    p = sub.add_parser("single-custom", help="Customized single-NIC state.json")
    p.add_argument("--trunk-if", required=True)
    p.add_argument("--lan-vlan-id", required=True)
    p.add_argument("--lan-cidr", required=True)
    p.add_argument("--lan-dhcp-start", required=True)
    p.add_argument("--lan-dhcp-end", required=True)
    p.add_argument("--wan-vlan-id", required=True)
    p.add_argument("--wan-mode", default="dhcp")
    p.add_argument("--wan-cidr")
    p.add_argument("--wan-gateway")
    p.add_argument("--mgmt-cidr", default="192.168.1.1/24")
    p.add_argument("--mgmt-dhcp-start", default="192.168.1.100")
    p.add_argument("--mgmt-dhcp-end", default="192.168.1.150")
    p.set_defaults(func=_cmd_single_custom)

    p = sub.add_parser("multi", help="Multi-NIC physical-port state.json")
    p.add_argument("--wan-if", required=True)
    p.add_argument("--lan-if", required=True)
    p.add_argument("--mgmt-if", default=None)
    p.add_argument("--lan-cidr", default="192.168.10.1/24")
    p.add_argument("--lan-dhcp-start", default="192.168.10.100")
    p.add_argument("--lan-dhcp-end", default="192.168.10.200")
    p.add_argument("--mgmt-cidr", default="192.168.1.1/24")
    p.add_argument("--mgmt-dhcp-start", default="192.168.1.100")
    p.add_argument("--mgmt-dhcp-end", default="192.168.1.150")
    p.set_defaults(func=_cmd_multi)

    p = sub.add_parser("validate", help="Validate a single value; prints normalized form or an error")
    p.add_argument("kind", choices=["vlan-id", "ip", "cidr", "dhcp-range"])
    p.add_argument("values", nargs="+")
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("suggest-dhcp-range", help="Suggest a DHCP start/end for a CIDR")
    p.add_argument("cidr")
    p.add_argument("end_offset", type=int, nargs="?", default=200)
    p.set_defaults(func=_cmd_suggest_dhcp_range)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
