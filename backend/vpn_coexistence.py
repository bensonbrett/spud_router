# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Cross-provider VPN coexistence checks.

Kept separate from any single provider's router/model so future providers
(WireGuard in #90, Nebula in #91) register their own predicate here rather
than editing Tailscale-specific code — this is the whole extension point.

Rule enforced today: at most one enabled VPN provider may be configured to
become the default route for all outbound traffic (Tailscale's exit-node
mode; WireGuard's AllowedIPs=0.0.0.0/0 client mode and any Nebula
equivalent will register their own check here when those land). Running
two "route everything through me" providers at once is never correct —
whichever one wins the routing table race silently drops the other's
supposed default-route status.
"""
from typing import Callable

# Each entry: (state key, predicate(provider_state) -> bool). The
# predicate receives just that provider's own state section, already
# known to be present and enabled.
ROUTE_ALL_CHECKS: list[tuple[str, Callable[[dict], bool]]] = [
    ("tailscale", lambda ts: bool(ts.get("exit_node"))),
]


def route_all_providers(state: dict) -> list[str]:
    """Return the names of every enabled provider currently configured to
    be the default route / exit node."""
    names = []
    for key, predicate in ROUTE_ALL_CHECKS:
        provider_state = state.get(key, {})
        if provider_state.get("enabled") and predicate(provider_state):
            names.append(key)
    return names


def validate_single_route_all(state: dict) -> None:
    """Raise ValueError if more than one VPN provider is configured to be
    the default route/exit node at the same time."""
    names = route_all_providers(state)
    if len(names) > 1:
        raise ValueError(
            "Only one VPN provider may be the default route/exit node at a "
            f"time (currently configured: {', '.join(names)})"
        )
