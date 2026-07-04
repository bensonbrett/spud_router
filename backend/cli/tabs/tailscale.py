# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Tailscale configuration tab."""
import getpass

from ..api import DELETE, GET, POST
from ..ui import (
    bold, dim, err, hi, ok, warn,
    clear, menu, pause, print_logo,
    print_status_bar, prompt, section, table,
)


def screen(state: dict) -> None:
    while True:
        clear()
        print_logo()
        print_status_bar(state)
        section("Tailscale")

        ts      = state.get("tailscale", {})
        enabled = ts.get("enabled", False)
        routes  = ts.get("advertise_routes", [])

        try:
            has_auth_key = GET("/api/tailscale").get("has_auth_key", False)
        except RuntimeError:
            has_auth_key = False

        table(["Setting", "Value"], [
            ["Enabled",          ok("yes") if enabled else dim("no")],
            ["Auth key",         ok("set") if has_auth_key else dim("not set")],
            ["Accept routes",    ok("yes") if ts.get("accept_routes") else dim("no")],
            ["Exit node",        ok("yes") if ts.get("exit_node") else dim("no")],
            ["Advertised routes", ", ".join(hi(r) for r in routes) if routes else dim("none")],
        ])
        print()

        # Live status from Tailscale binary
        try:
            live = GET("/api/tailscale/status")
            if "error" not in live:
                self_node = live.get("Self", {})
                peers     = live.get("Peer", {})
                print(f"  {bold('This device:')} {hi(self_node.get('DNSName','?'))}  "
                      f"{dim(', '.join(self_node.get('TailscaleIPs',[])))}")
                for p in peers.values():
                    dot = ok("●") if p.get("Online") else dim("○")
                    print(f"    {dot} {p.get('DNSName','?')}  "
                          f"{dim(', '.join(p.get('TailscaleIPs',[])))} ")
            elif live.get("error") == "tailscale not installed":
                print(warn("  Tailscale not installed."))
                print(dim("  Run: curl -fsSL https://tailscale.com/install.sh | sh"))
        except RuntimeError:
            pass

        idx = menu("Tailscale Actions", [
            ("Toggle enable/disable", ""),
            ("Edit advertised routes", ""),
            ("Toggle exit node",       ""),
            ("Toggle accept routes",   ""),
            ("Set/replace auth key",   ""),
            ("Clear auth key",         ""),
            ("Reload",                 ""),
        ])
        if idx == -1:
            return
        if idx == 0:
            _toggle(ts, "enabled", f"Tailscale {'disabled' if enabled else 'enabled'}")
        elif idx == 1:
            _edit_routes(ts)
        elif idx == 2:
            _toggle(ts, "exit_node", f"Exit node {'disabled' if ts.get('exit_node') else 'enabled'}")
        elif idx == 3:
            _toggle(ts, "accept_routes", f"Accept routes {'disabled' if ts.get('accept_routes') else 'enabled'}")
        elif idx == 4:
            _set_authkey()
        elif idx == 5:
            _clear_authkey()
        state = GET("/api/state")


def _toggle(ts: dict, key: str, success_msg: str) -> None:
    try:
        POST("/api/tailscale", {**ts, key: not ts.get(key, False)})
        print(ok(f"\n  ✓ {success_msg}"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _edit_routes(ts: dict) -> None:
    section("Advertised Routes")
    routes = list(ts.get("advertise_routes", []))

    while True:
        print()
        if routes:
            for i, r in enumerate(routes, 1):
                print(f"  {i}. {hi(r)}")
        else:
            print(dim("  No routes advertised"))
        print(dim("\n  Enter a CIDR to add, a number to remove, or Enter to save"))

        try:
            val = prompt("").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not val:
            break

        try:
            i = int(val) - 1
            if 0 <= i < len(routes):
                removed = routes.pop(i)
                print(dim(f"  Removed {removed}"))
            else:
                print(err("  Invalid number"))
        except ValueError:
            if val not in routes:
                routes.append(val)
                print(ok(f"  Added {val}"))

    try:
        POST("/api/tailscale", {**ts, "advertise_routes": routes})
        print(ok("\n  ✓ Routes saved"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _set_authkey() -> None:
    section("Set/Replace Auth Key")
    print(dim("  Paste a pre-created, reusable, non-ephemeral auth key from the"))
    print(dim("  Tailscale admin console. Input is hidden.\n"))
    try:
        key = getpass.getpass("  › Auth key: ").strip()
    except (KeyboardInterrupt, EOFError):
        return
    if not key:
        print(err("\n  No key entered"))
        pause()
        return
    try:
        POST("/api/tailscale/authkey", {"auth_key": key})
        print(ok("\n  ✓ Auth key saved"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _clear_authkey() -> None:
    try:
        DELETE("/api/tailscale/authkey")
        print(ok("\n  ✓ Auth key cleared (node not logged out)"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()
