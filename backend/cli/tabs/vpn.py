# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
VPN menu — per-provider sub-screens. Providers are enabled/configured
completely independently of each other (mirrors the Web UI's VpnTab.jsx:
no single "provider" selector, each keeps its own `enabled` flag) and
coexist on the router at the same time.

Tailscale is fully wired below (delegates to tabs/tailscale.py); WireGuard
and Nebula land in later releases (#90, #91) as their own sub-screen with
the same shape.
"""
from ..api import GET
from ..ui import (
    dim, ok,
    clear, menu, pause, print_logo,
    print_status_bar, section,
)
from . import tailscale as tailscale_tab


def screen(state: dict) -> None:
    while True:
        clear()
        print_logo()
        print_status_bar(state)
        section("VPN")

        ts_enabled = state.get("tailscale", {}).get("enabled", False)

        idx = menu("VPN Providers", [
            ("Tailscale", ok("enabled") if ts_enabled else dim("disabled")),
            ("WireGuard", dim("coming soon")),
            ("Nebula",    dim("coming soon")),
        ])
        if idx == -1:
            return
        if idx == 0:
            tailscale_tab.screen(state)
        elif idx == 1:
            _coming_soon("WireGuard")
        elif idx == 2:
            _coming_soon("Nebula")
        state = GET("/api/state")


def _coming_soon(name: str) -> None:
    section(name)
    print(dim(f"  {name} support is coming in a future release."))
    pause()
