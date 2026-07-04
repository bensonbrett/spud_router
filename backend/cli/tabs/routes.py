# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Static routes tab."""
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
        section("Static Routes")

        routes = state.get("static_routes", [])
        if routes:
            table(
                ["Destination", "Via", "Interface", "Description"],
                [[r["destination"], r["gateway"], r.get("interface", "—"), r.get("description", "")] for r in routes],
            )
        else:
            print(dim("  No static routes."))

        idx = menu("Route Actions", [
            ("Add route",    ""),
            ("Remove route", ""),
            ("Reload",       ""),
        ])
        if idx == -1:
            return
        if idx == 0:
            _add(state)
        elif idx == 1:
            _delete(state)
        state = GET("/api/state")


def _add(state: dict) -> None:
    section("Add Static Route")
    vlans = state.get("vlans", [])
    if vlans:
        print(dim("  Interfaces: " + ", ".join(f"{v['interface']}.{v['vlan_id']}" for v in vlans)))

    try:
        dest  = prompt("Destination CIDR (e.g. 10.0.0.0/8)")
        gw    = prompt("Via gateway IP")
        iface = prompt("Interface (optional, Enter to skip)")
        desc  = prompt("Description (optional)")
    except (KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        POST("/api/routes", {
            "destination": dest,
            "gateway":     gw,
            "interface":   iface or None,
            "description": desc,
        })
        print(ok(f"\n  ✓ Route to {dest} added"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _delete(state: dict) -> None:
    routes = state.get("static_routes", [])
    if not routes:
        print(dim("  No routes to remove."))
        pause()
        return

    idx = menu(
        "Remove route",
        [(r["destination"], f"via {r['gateway']} {r.get('description','')}") for r in routes],
        "Cancel",
    )
    if idx == -1:
        return

    r = routes[idx]
    try:
        DELETE(f"/api/routes/{urllib.parse.quote(r['destination'], safe='')}")
        print(ok(f"\n  ✓ Route to {r['destination']} removed"))
    except RuntimeError as ex:
        print(err(f"\n  Error: {ex}"))
    pause()
