"""Live status tab — interfaces, routing table, DHCP leases, and diagnostics."""
from ..api import GET
from ..ui import (
    bold, dim, hi, ok, warn,
    clear, pause, print_logo, section, table,
)


def screen() -> None:
    clear()
    print_logo()
    section("Live Status")

    try:
        status = GET("/api/status")
    except RuntimeError as e:
        print(f"  Error fetching status: {e}")
        pause()
        return

    # Interfaces
    print(f"  {bold('Interfaces')}")
    for line in status.get("interfaces", "").strip().split("\n"):
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            state_col = ok if parts[1] == "UP" else warn
            print(
                f"    {hi(f'{parts[0]:<16}')}"
                f" {state_col(f'{parts[1]:<10}')}"
                f" {dim(' '.join(parts[2:]))}"
            )

    # Routing table
    print(f"\n  {bold('Routing Table')}")
    for line in status.get("routes", "").strip().split("\n"):
        if line:
            print(f"    {dim(line)}")

    # DHCP leases
    leases = status.get("leases", [])
    print(f"\n  {bold(f'DHCP Leases ({len(leases)})')}")
    if leases:
        table(
            ["IP", "MAC", "Hostname"],
            [[l["ip"], l["mac"], l["hostname"]] for l in leases],
        )
    else:
        print(dim("  No active leases."))

    # Per-VLAN/WAN diagnostics
    print(f"\n  {bold('Diagnostics')}")
    try:
        diag = GET("/api/diagnostics")
    except RuntimeError:
        print(f"    {warn('Could not fetch diagnostics.')}")
        pause()
        return

    default_route = diag.get("default_route", "")
    if default_route:
        print(f"    {dim('Default route:')} {default_route}")
    else:
        print(f"    {warn('No default route.')}")

    all_ifaces = []
    if diag.get("wan"):
        all_ifaces.append(diag["wan"])
    all_ifaces.extend(diag.get("vlans", []))

    for iface in all_ifaces:
        is_up   = iface.get("carrier") is True and iface.get("operstate") == "up"
        status_marker = ok("UP  ") if is_up else warn("DOWN")
        role_tag = "WAN" if iface.get("role") == "wan" else f"VLAN {iface.get('vlan_id', '')} {iface.get('vlan_name', '')}"
        name_col = hi(f"{iface['name']:<16}")
        addrs    = ", ".join(iface.get("addresses") or []) or dim("no address")
        print(f"\n    {name_col}  {status_marker}  {dim(role_tag)}")
        print(f"    {'':<16}  {dim('addr:')} {addrs}")
        if iface.get("cfg_address") and not iface.get("ip_present"):
            print(f"    {'':<16}  {warn(f'configured {iface[\"cfg_address\"]} not assigned')}")
        if iface.get("is_default_gw"):
            print(f"    {'':<16}  {ok('is default gateway')}")
        vlan_leases = iface.get("leases", [])
        if vlan_leases:
            print(f"    {'':<16}  {dim('leases:')} {len(vlan_leases)}")
        hint = iface.get("hint")
        if hint:
            print(f"    {warn('⚠')} {hint}")

    pause()
