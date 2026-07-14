# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Web UI (tcp/8080) reachability guard (#209).

Mirrors vpn_coexistence.py's shape: a small, state-wide invariant that no
single pydantic model can enforce on its own, since it spans RouterConfig
(the management interface) and every VlanConfig entry (LAN networks) at
once. Routers building a "prospective state" (current state + the one
section about to change) call validate_web_ui_reachable() on it before
persisting, same pattern as vpn_coexistence.validate_single_route_all.

Rule enforced: once the box has at least one enabled, addressable network
(a LAN VLAN or an enabled management interface), at least one of them must
keep the web UI reachable. An unconfigured box (no VLANs yet, mgmt not
enabled — e.g. mid initial-setup, WAN-only so far) has nothing to be
"locked out" of yet, so it's deliberately exempt — only refuse a change
that would take a box with a reachable UI down to zero. SSH + the spud-cli
TUI remain a real recovery path, but per #213's lesson this refuses the
all-off config outright rather than relying on that.
"""


def web_ui_reachable(state: dict) -> bool:
    """True if at least one enabled network keeps the web UI open."""
    router = state.get("router", {})
    if router.get("mgmt_enabled") and router.get("mgmt_web_ui", True):
        return True
    return any(
        vlan.get("web_ui", True)
        for vlan in state.get("vlans", [])
        if vlan.get("ip_address")   # skip WAN-marker entries (no address)
    )


def _has_any_addressable_interface(state: dict) -> bool:
    router = state.get("router", {})
    if router.get("mgmt_enabled"):
        return True
    return any(vlan.get("ip_address") for vlan in state.get("vlans", []))


def validate_web_ui_reachable(state: dict) -> None:
    """Raise ValueError if the prospective state has at least one
    addressable network but the web UI is off on all of them."""
    if not _has_any_addressable_interface(state):
        return
    if not web_ui_reachable(state):
        raise ValueError(
            "This would disable the web UI on every interface — refusing to avoid "
            "a lockout. Keep 'Allow web UI' enabled on at least one network (a LAN "
            "VLAN or the management interface)."
        )
