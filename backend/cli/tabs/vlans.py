"""VLAN management tab."""
import urllib.parse

from ..api import DELETE, POST, PUT, GET
from ..ui import (
    bold, dim, err, hi, ok, warn,
    clear, confirm, menu, pause, print_logo,
    print_status_bar, prompt, section, table,
)


def screen(state: dict) -> None:
    while True:
        clear()
        print_logo()
        print_status_bar(state)
        section("VLANs")

        vlans = state.get("vlans", [])
        if vlans:
            rows = []
            for v in vlans:
                rows.append([
                    str(v["vlan_id"]),
                    v["name"],
                    f"{v['interface']}.{v['vlan_id']}",
                    f"{v['ip_address']}/{v['prefix_len']}",
                    warn("isolated") if v.get("isolate") else ok("routed"),
                    ok("dhcp") if v.get("dhcp_enabled") else dim("static"),
                ])
            table(["ID", "Name", "Interface", "Gateway", "Mode", "DHCP"], rows)
        else:
            print(dim("  No VLANs configured."))

        idx = menu("VLAN Actions", [
            ("Add VLAN",    ""),
            ("Edit VLAN",   ""),
            ("Remove VLAN", ""),
            ("Reload",      "Refresh from backend"),
        ])
        if idx == -1:
            return
        if idx == 0:
            _add()
        elif idx == 1:
            _edit(state)
        elif idx == 2:
            _delete(state)
        state = GET("/api/state")


def _add() -> None:
    section("Add VLAN")
    try:
        vlan_id = int(prompt("VLAN ID (1–4094)"))
        name    = prompt("Name (e.g. Trusted)")
        iface   = prompt("Parent interface", "eth0")
        ip      = prompt("Gateway IP (e.g. 192.168.10.1)")
        prefix  = int(prompt("Prefix length", "24"))
        dhcp    = confirm("Enable DHCP?")
        dhcp_start = dhcp_end = ""
        if dhcp:
            net        = ".".join(ip.split(".")[:3])
            dhcp_start = prompt("DHCP start", f"{net}.100")
            dhcp_end   = prompt("DHCP end",   f"{net}.200")
        lease   = prompt("DHCP lease", "12h") if dhcp else "12h"
        isolate = confirm("Isolate this VLAN (block inter-VLAN routing)?")
        icmp_echo = confirm("Allow ping (ICMP echo) on this VLAN?")
    except (ValueError, KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        POST("/api/vlans", {
            "vlan_id": vlan_id, "name": name, "interface": iface,
            "ip_address": ip, "prefix_len": prefix,
            "dhcp_enabled": dhcp, "dhcp_start": dhcp_start,
            "dhcp_end": dhcp_end, "dhcp_lease": lease, "isolate": isolate,
            "icmp_echo": icmp_echo,
        })
        print(ok(f"\n  ✓ VLAN {vlan_id} ({name}) added"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _edit(state: dict) -> None:
    # WAN VLANs (no gateway IP, addressed via DHCP from the ISP) have nothing
    # here to edit — mirrors the web UI, which only shows Edit on LAN VLANs.
    vlans = [v for v in state.get("vlans", []) if v.get("ip_address")]
    if not vlans:
        print(dim("  No editable VLANs (WAN VLANs cannot be edited here)."))
        pause()
        return

    idx = menu(
        "Edit VLAN",
        [(f"VLAN {v['vlan_id']} — {v['name']}", f"{v['ip_address']}/{v['prefix_len']}") for v in vlans],
        "Cancel",
    )
    if idx == -1:
        return

    v = vlans[idx]
    section(f"Edit VLAN {v['vlan_id']} ({v['name']})")

    try:
        name   = prompt("Name", v["name"])
        iface  = prompt("Parent interface", v["interface"])
        ip     = prompt("Gateway IP", v["ip_address"])
        prefix = int(prompt("Prefix length", str(v["prefix_len"])))

        dhcp_currently = v.get("dhcp_enabled", True)
        dhcp = (
            confirm("Enable DHCP?")
            if not dhcp_currently
            else confirm("Currently DHCP enabled — disable it?") == False
        )
        dhcp_start = v.get("dhcp_start", "")
        dhcp_end   = v.get("dhcp_end", "")
        lease      = v.get("dhcp_lease", "12h")
        dns_server = v.get("dns_server", "")
        if dhcp:
            net        = ".".join(ip.split(".")[:3])
            dhcp_start = prompt("DHCP start", dhcp_start or f"{net}.100")
            dhcp_end   = prompt("DHCP end",   dhcp_end or f"{net}.200")
            lease      = prompt("DHCP lease", lease)
            dns_server = prompt("Custom DNS server (blank = this VLAN's gateway)", dns_server)

        isolate_currently = v.get("isolate", False)
        isolate = (
            confirm("Isolate this VLAN (block inter-VLAN routing)?")
            if not isolate_currently
            else confirm("Currently isolated — allow inter-VLAN routing?") == False
        )

        icmp_echo_currently = v.get("icmp_echo", False)
        icmp_echo = (
            confirm("Allow ping (ICMP echo) on this VLAN?")
            if not icmp_echo_currently
            else confirm("Ping currently allowed — block it?") == False
        )

        dhcp_options = list(v.get("dhcp_options", []))
        if confirm("Edit custom DHCP options?"):
            dhcp_options = _edit_dhcp_options(dhcp_options)
    except (ValueError, KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        PUT(f"/api/vlans/{v['vlan_id']}", {
            "vlan_id": v["vlan_id"], "name": name, "interface": iface,
            "ip_address": ip, "prefix_len": prefix,
            "dhcp_enabled": dhcp, "dhcp_start": dhcp_start,
            "dhcp_end": dhcp_end, "dhcp_lease": lease, "isolate": isolate,
            "dns_server": dns_server, "dhcp_options": dhcp_options,
            "icmp_echo": icmp_echo,
        })
        print(ok(f"\n  ✓ VLAN {v['vlan_id']} updated"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _edit_dhcp_options(options: list[str]) -> list[str]:
    """Add/remove raw dnsmasq dhcp-option values, e.g. '42,192.168.10.1' for NTP."""
    options = list(options)
    section("Custom DHCP Options")

    while True:
        print()
        if options:
            for i, o in enumerate(options, 1):
                print(f"  {i}. {hi(o)}")
        else:
            print(dim("  No custom DHCP options"))
        print(dim("\n  Enter a value (e.g. 42,192.168.10.1) to add, a number to remove, or Enter to finish"))

        try:
            val = prompt("").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not val:
            break

        try:
            i = int(val) - 1
            if 0 <= i < len(options):
                removed = options.pop(i)
                print(dim(f"  Removed {removed}"))
            else:
                print(err("  Invalid number"))
        except ValueError:
            if val not in options:
                options.append(val)
                print(ok(f"  Added {val}"))

    return options


def _delete(state: dict) -> None:
    vlans = state.get("vlans", [])
    if not vlans:
        print(dim("  No VLANs to remove."))
        pause()
        return

    idx = menu(
        "Remove VLAN",
        [(f"VLAN {v['vlan_id']} — {v['name']}", f"{v['ip_address']}/{v['prefix_len']}") for v in vlans],
        "Cancel",
    )
    if idx == -1:
        return

    v = vlans[idx]
    if not confirm(f"Remove VLAN {v['vlan_id']} ({v['name']})?"):
        return

    try:
        DELETE(f"/api/vlans/{v['vlan_id']}")
        print(ok(f"\n  ✓ VLAN {v['vlan_id']} removed"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()
