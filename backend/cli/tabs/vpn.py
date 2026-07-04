# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
VPN menu — per-provider sub-screens. Providers are enabled/configured
completely independently of each other (mirrors the Web UI's VpnTab.jsx:
no single "provider" selector, each keeps its own `enabled` flag) and
coexist on the router at the same time.

Tailscale, WireGuard, and Nebula each delegate to their own tabs/*.py
sub-screen with the same shape.
"""
from ..api import GET
from ..ui import (
    dim, ok,
    clear, menu, print_logo,
    print_status_bar, section,
)
from . import nebula as nebula_tab
from . import tailscale as tailscale_tab
from . import wireguard as wireguard_tab


def screen(state: dict) -> None:
    while True:
        clear()
        print_logo()
        print_status_bar(state)
        section("VPN")

        ts_enabled = state.get("tailscale", {}).get("enabled", False)
        wg_enabled = state.get("wireguard", {}).get("enabled", False)
        nb_enabled = state.get("nebula", {}).get("enabled", False)

        idx = menu("VPN Providers", [
            ("Tailscale", ok("enabled") if ts_enabled else dim("disabled")),
            ("WireGuard", ok("enabled") if wg_enabled else dim("disabled")),
            ("Nebula",    ok("enabled") if nb_enabled else dim("disabled")),
        ])
        if idx == -1:
            return
        if idx == 0:
            tailscale_tab.screen(state)
        elif idx == 1:
            wireguard_tab.screen(state)
        elif idx == 2:
            nebula_tab.screen(state)
        state = GET("/api/state")
