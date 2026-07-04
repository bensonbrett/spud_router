# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Local DNS entries tab."""
import urllib.parse

from ..api import DELETE, GET, POST
from ..ui import (
    dim, err, hi, ok,
    clear, menu, pause, print_logo,
    print_status_bar, prompt, section, table,
)


def screen(state: dict) -> None:
    while True:
        clear()
        print_logo()
        print_status_bar(state)
        section("Local DNS Entries")

        entries = state.get("dns_entries", [])
        domain  = f"{state.get('router', {}).get('hostname', 'spud-router')}.lan"
        print(dim(f"  Domain: {domain}\n"))

        if entries:
            table(
                ["Hostname", "IP", "Description"],
                [[e["hostname"], e["ip"], e.get("description", "")] for e in entries],
            )
        else:
            print(dim("  No custom DNS entries."))

        idx = menu("DNS Actions", [
            ("Add entry",    ""),
            ("Remove entry", ""),
            ("Reload",       ""),
        ])
        if idx == -1:
            return
        if idx == 0:
            _add(domain)
        elif idx == 1:
            _delete(state)
        state = GET("/api/state")


def _add(domain: str) -> None:
    section("Add DNS Entry")
    try:
        hostname = prompt("Hostname (e.g. nas)")
        ip       = prompt("IP address")
        desc     = prompt("Description (optional)")
    except (KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        POST("/api/dns", {"hostname": hostname, "ip": ip, "description": desc})
        print(ok(f"\n  ✓ {hostname}.{domain} → {ip}"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _delete(state: dict) -> None:
    entries = state.get("dns_entries", [])
    if not entries:
        print(dim("  Nothing to remove."))
        pause()
        return

    idx = menu(
        "Remove entry",
        [(e["hostname"], f"{e['ip']} {e.get('description','')}") for e in entries],
        "Cancel",
    )
    if idx == -1:
        return

    e = entries[idx]
    try:
        DELETE(f"/api/dns/{urllib.parse.quote(e['hostname'])}")
        print(ok(f"\n  ✓ {e['hostname']} removed"))
    except RuntimeError as ex:
        print(err(f"\n  Error: {ex}"))
    pause()
