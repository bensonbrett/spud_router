# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Live status tab — interfaces, routing table, DHCP leases, and diagnostics."""
from ..api import GET, POST
from ..ui import (
    bold, dim, err, hi, menu, ok, warn,
    clear, pause, print_logo, prompt, section, table,
)

DIAG_COMMAND_OPTS = ("ping", "traceroute", "nslookup")


def screen() -> None:
    while True:
        _show_status()
        idx = menu("Diagnostics", [
            ("Run a command (ping/traceroute/nslookup)", ""),
        ], back_label="Back")
        if idx == -1:
            return
        if idx == 0:
            _run_command()


def _show_status() -> None:
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
            cfg_addr = iface["cfg_address"]
            print(f"    {'':<16}  {warn(f'configured {cfg_addr} not assigned')}")
        if iface.get("is_default_gw"):
            print(f"    {'':<16}  {ok('is default gateway')}")
        vlan_leases = iface.get("leases", [])
        if vlan_leases:
            print(f"    {'':<16}  {dim('leases:')} {len(vlan_leases)}")
        hint = iface.get("hint")
        if hint:
            print(f"    {warn('⚠')} {hint}")


def _run_command() -> None:
    section("Run Diagnostic Command")
    print(dim(f"  Commands: {', '.join(DIAG_COMMAND_OPTS)}"))
    try:
        command = prompt("Command [ping/traceroute/nslookup]", "ping")
        target  = prompt("Target (host or IP)")
    except (KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    if command not in DIAG_COMMAND_OPTS:
        print(err(f"  Invalid command: {command}"))
        pause()
        return
    if not target:
        print(err("  Target is required."))
        pause()
        return

    print(dim("\n  Running…"))
    try:
        result = POST("/api/diagnostics/run", {"command": command, "target": target})
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
        pause()
        return

    if result.get("timed_out"):
        print(warn("\n  ⚠ Command timed out — showing partial output."))
    if result.get("truncated"):
        print(warn("  ⚠ Output truncated."))
    print()
    for line in (result.get("output") or "(no output)").splitlines():
        print(f"  {line}")
    pause()
