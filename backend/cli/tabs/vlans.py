"""VLAN management tab."""
import urllib.parse

from ..api import DELETE, POST, GET
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
            ("Remove VLAN", ""),
            ("Reload",      "Refresh from backend"),
        ])
        if idx == -1:
            return
        if idx == 0:
            _add()
        elif idx == 1:
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
    except (ValueError, KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        POST("/api/vlans", {
            "vlan_id": vlan_id, "name": name, "interface": iface,
            "ip_address": ip, "prefix_len": prefix,
            "dhcp_enabled": dhcp, "dhcp_start": dhcp_start,
            "dhcp_end": dhcp_end, "dhcp_lease": lease, "isolate": isolate,
        })
        print(ok(f"\n  ✓ VLAN {vlan_id} ({name}) added"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


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
