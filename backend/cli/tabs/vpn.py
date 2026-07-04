# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
VPN menu — per-provider sub-screens. Providers are enabled/configured
completely independently of each other (mirrors the Web UI's VpnTab.jsx:
no single "provider" selector, each keeps its own `enabled` flag) and
coexist on the router at the same time.

Tailscale and WireGuard are fully wired below (delegate to tabs/tailscale.py
and tabs/wireguard.py); Nebula lands in a later release (#91) as its own
sub-screen with the same shape.
"""
from ..api import GET
from ..ui import (
    dim, ok,
    clear, menu, pause, print_logo,
    print_status_bar, section,
)
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

        idx = menu("VPN Providers", [
            ("Tailscale", ok("enabled") if ts_enabled else dim("disabled")),
            ("WireGuard", ok("enabled") if wg_enabled else dim("disabled")),
            ("Nebula",    dim("coming soon")),
        ])
        if idx == -1:
            return
        if idx == 0:
            tailscale_tab.screen(state)
        elif idx == 1:
            wireguard_tab.screen(state)
        elif idx == 2:
            _coming_soon("Nebula")
        state = GET("/api/state")


def _coming_soon(name: str) -> None:
    section(name)
    print(dim(f"  {name} support is coming in a future release."))
    pause()
