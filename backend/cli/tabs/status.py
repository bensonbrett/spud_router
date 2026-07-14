# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Live status tab — interfaces, routing table, DHCP leases, and diagnostics."""
import re

from ..api import GET, POST
from ..ui import (
    bold, confirm, dim, err, hi, menu, ok, warn,
    clear, pause, print_logo, prompt, section, table,
)

DIAG_COMMAND_OPTS = ("ping", "traceroute", "nslookup")

# Client-side mirror of models.py's WolRequest MAC regex — just fast
# feedback before the round trip; the backend re-validates and normalizes
# independently.
_MAC_RE = re.compile(r'^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$')


def screen() -> None:
    while True:
        _show_status()
        idx = menu("Diagnostics", [
            ("Run a command (ping/traceroute/nslookup)", ""),
            ("Wake-on-LAN", ""),
        ], back_label="Back")
        if idx == -1:
            return
        if idx == 0:
            _run_command()
        elif idx == 1:
            _run_wol()


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

    # System monitor — memory/load/CPU/disk/interface counters, read
    # straight from /proc by the backend (see /api/system/monitor).
    print(f"\n  {bold('System Monitor')}")
    try:
        monitor = GET("/api/system/monitor")
    except RuntimeError:
        print(f"    {warn('Could not fetch system monitor.')}")
        monitor = None

    if monitor is not None:
        mem = monitor.get("memory")
        if mem:
            total = mem.get("mem_total_kb") or 0
            used  = mem.get("mem_used_kb") or 0
            pct   = (used / total * 100) if total else 0
            pct_col = warn if pct >= 85 else ok
            print(
                f"    {dim('Memory:')} {pct_col(f'{pct:.0f}%')}"
                f"  ({used // 1024} MB / {total // 1024} MB)"
            )
        else:
            print(f"    {dim('Memory:')} {warn('unavailable')}")

        load = monitor.get("load")
        if load:
            print(
                f"    {dim('Load avg (1/5/15m):')} "
                f"{load['load1']:.2f} / {load['load5']:.2f} / {load['load15']:.2f}"
            )
        else:
            print(f"    {dim('Load avg:')} {warn('unavailable')}")

        cpu = monitor.get("cpu_percent")
        if cpu is not None:
            cpu_col = warn if cpu >= 85 else ok
            print(f"    {dim('CPU (aggregate):')} {cpu_col(f'{cpu:.1f}%')}")
        else:
            print(f"    {dim('CPU:')} {warn('unavailable')}")

        disks = monitor.get("disks") or {}
        if disks:
            disk_rows = []
            for label, d in disks.items():
                total_b = d.get("total_bytes") or 0
                used_b  = d.get("used_bytes") or 0
                pct_b   = (used_b / total_b * 100) if total_b else 0
                disk_rows.append([
                    label,
                    f"{pct_b:.0f}%",
                    f"{used_b / (1024**3):.1f} GB / {total_b / (1024**3):.1f} GB",
                ])
            print(f"    {dim('Disks:')}")
            table(["Mount", "Used", "Size"], disk_rows)
        else:
            print(f"    {dim('Disks:')} {warn('unavailable')}")

        ifaces = monitor.get("interfaces") or {}
        if ifaces:
            iface_rows = [
                [
                    name,
                    str(c.get("rx_bytes", 0)),
                    str(c.get("rx_packets", 0)),
                    f"{c.get('rx_errs', 0)}/{c.get('rx_drop', 0)}",
                    str(c.get("tx_bytes", 0)),
                    str(c.get("tx_packets", 0)),
                    f"{c.get('tx_errs', 0)}/{c.get('tx_drop', 0)}",
                ]
                for name, c in ifaces.items()
            ]
            print(f"    {dim('Interface counters:')}")
            table(
                ["Interface", "RX bytes", "RX pkts", "RX err/drop", "TX bytes", "TX pkts", "TX err/drop"],
                iface_rows,
            )
        else:
            print(f"    {dim('Interface counters:')} {dim('(none)')}")

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


def _run_wol() -> None:
    section("Wake-on-LAN")
    try:
        mac = prompt("MAC address (e.g. aa:bb:cc:dd:ee:ff)")
        vlan_id_str = prompt("VLAN ID (blank = broadcast on all interfaces)")
        broadcast = ""
        if not vlan_id_str:
            broadcast = prompt("Custom broadcast address (blank = 255.255.255.255)")
    except (KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    if not _MAC_RE.match(mac):
        print(err(f"  Invalid MAC address: {mac}"))
        pause()
        return

    body = {"mac": mac}
    if vlan_id_str:
        try:
            body["vlan_id"] = int(vlan_id_str)
        except ValueError:
            print(err(f"  Invalid VLAN ID: {vlan_id_str}"))
            pause()
            return
    elif broadcast:
        body["broadcast"] = broadcast

    if not confirm(f"  Send a Wake-on-LAN magic packet to {mac}?"):
        print(dim("  Cancelled."))
        return

    try:
        result = POST("/api/diagnostics/wol", body)
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
        pause()
        return

    if result.get("sent"):
        print(ok(f"\n  ✓ Magic packet sent to {result['mac']} via {result['broadcast']}"))
    else:
        print(err(f"\n  ✗ Failed to send: {result.get('error', 'unknown error')}"))
    pause()
