# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Static routes tab (also hosts the BGP section — see _bgp_screen)."""
import urllib.parse

from ..api import DELETE, GET, POST, PUT
from ..ui import (
    dim, err, hi, ok, warn,
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
            ("BGP",          ""),
            ("Reload",       ""),
        ])
        if idx == -1:
            return
        if idx == 0:
            _add(state)
        elif idx == 1:
            _delete(state)
        elif idx == 2:
            _bgp_screen(state)
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


# ── BGP (issue #143) ──────────────────────────────────────────────────────────
# GET/PUT /api/bgp is one config blob (no per-neighbor/network sub-resource,
# same as the web UI) — every mutation here reads the current config, edits
# it, and PUTs the whole thing back.

def _bgp_screen(state: dict) -> None:
    while True:
        clear()
        print_logo()
        print_status_bar(state)
        section("BGP")

        try:
            cfg = GET("/api/bgp")
        except RuntimeError as e:
            print(err(f"\n  Error loading config: {e}"))
            pause()
            return

        try:
            status = GET("/api/bgp/status")
        except RuntimeError:
            status = {"enabled": cfg.get("enabled", False), "running": False, "neighbors": []}
        status_by_ip = {n["ip"]: n for n in status.get("neighbors", [])}

        neighbors = cfg.get("neighbors", [])
        networks = cfg.get("networks", [])

        table(["Setting", "Value"], [
            ["Enabled",   ok("yes") if cfg.get("enabled") else dim("no")],
            ["ASN",       hi(str(cfg.get("asn"))) if cfg.get("asn") is not None else dim("not set")],
            ["Router ID", hi(cfg.get("router_id") or dim("not set"))],
            ["FRR",       ok("running") if status.get("running") else dim("not running")],
            ["Neighbors", hi(str(len(neighbors)))],
            ["Networks",  hi(str(len(networks)))],
        ])
        print()

        if neighbors:
            table(["Neighbor", "Remote AS", "Description", "Session", "Pfx rcvd/sent"], [
                [
                    n["ip"], str(n["remote_as"]), n.get("description") or "—",
                    (status_by_ip.get(n["ip"], {}).get("state") or dim("—")),
                    f"{status_by_ip.get(n['ip'], {}).get('pfx_rcvd', '—')}/{status_by_ip.get(n['ip'], {}).get('pfx_sent', '—')}",
                ]
                for n in neighbors
            ])
            print()

        if networks:
            table(["Advertised network"], [[n] for n in networks])
            print()

        idx = menu("BGP Actions", [
            ("Toggle enable/disable", ""),
            ("Set ASN",               ""),
            ("Set router ID",         ""),
            ("Add neighbor",          ""),
            ("Remove neighbor",       ""),
            ("Add advertised network",    ""),
            ("Remove advertised network", ""),
            ("Reload",                ""),
        ])
        if idx == -1:
            return
        if idx == 0:
            _bgp_save(cfg, enabled=not cfg.get("enabled", False))
        elif idx == 1:
            _bgp_set_asn(cfg)
        elif idx == 2:
            _bgp_set_router_id(cfg)
        elif idx == 3:
            _bgp_add_neighbor(cfg)
        elif idx == 4:
            _bgp_remove_neighbor(cfg)
        elif idx == 5:
            _bgp_add_network(cfg)
        elif idx == 6:
            _bgp_remove_network(cfg)


def _bgp_save(cfg: dict, **changes) -> None:
    body = {
        "enabled":   cfg.get("enabled", False),
        "asn":       cfg.get("asn"),
        "router_id": cfg.get("router_id"),
        "neighbors": cfg.get("neighbors", []),
        "networks":  cfg.get("networks", []),
        **changes,
    }
    try:
        PUT("/api/bgp", body)
        print(ok("\n  ✓ Saved"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _bgp_set_asn(cfg: dict) -> None:
    val = prompt("Local ASN", str(cfg.get("asn") or ""))
    try:
        asn = int(val)
    except ValueError:
        print(err("\n  Invalid ASN"))
        pause()
        return
    _bgp_save(cfg, asn=asn)


def _bgp_set_router_id(cfg: dict) -> None:
    val = prompt("Router ID (IPv4)", cfg.get("router_id") or "")
    _bgp_save(cfg, router_id=val or None)


def _bgp_add_neighbor(cfg: dict) -> None:
    section("Add BGP Neighbor")
    try:
        ip = prompt("Neighbor IP")
        remote_as_raw = prompt("Remote AS")
        desc = prompt("Description (optional)")
    except (KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return
    try:
        remote_as = int(remote_as_raw)
    except ValueError:
        print(err("\n  Invalid remote AS"))
        pause()
        return
    neighbors = cfg.get("neighbors", []) + [{"ip": ip, "remote_as": remote_as, "description": desc}]
    _bgp_save(cfg, neighbors=neighbors)


def _bgp_remove_neighbor(cfg: dict) -> None:
    neighbors = cfg.get("neighbors", [])
    if not neighbors:
        print(dim("\n  No neighbors to remove"))
        pause()
        return
    idx = menu(
        "Remove which neighbor?",
        [(n["ip"], f"AS {n['remote_as']} {n.get('description', '')}") for n in neighbors],
        "Cancel",
    )
    if idx == -1:
        return
    remaining = [n for i, n in enumerate(neighbors) if i != idx]
    _bgp_save(cfg, neighbors=remaining)


def _bgp_add_network(cfg: dict) -> None:
    val = prompt("Network CIDR to advertise (e.g. 10.0.0.0/24)")
    if not val:
        return
    networks = cfg.get("networks", [])
    if val in networks:
        print(warn("\n  Already advertised"))
        pause()
        return
    _bgp_save(cfg, networks=networks + [val])


def _bgp_remove_network(cfg: dict) -> None:
    networks = cfg.get("networks", [])
    if not networks:
        print(dim("\n  No advertised networks to remove"))
        pause()
        return
    idx = menu("Remove which network?", [(n, "") for n in networks], "Cancel")
    if idx == -1:
        return
    remaining = [n for i, n in enumerate(networks) if i != idx]
    _bgp_save(cfg, networks=remaining)
