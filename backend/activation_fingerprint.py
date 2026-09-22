# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Dependency-free fingerprints for configuration that Apply activates."""
import hashlib
import json


UNSAFE_GENERATOR_KEYS = (
    "netplan", "dnsmasq", "iptables", "hostapd", "syslog", "snmp", "doh",
    "bgp", "wireguard", "nebula",
)


def unsafe_hash(state: dict, generated: dict) -> str:
    """Return a stable hash of every connectivity-affecting activation input.

    Tailscale is imperative rather than a generated file, so its effective
    command options are serialized separately. VPN secrets remain inside the
    one-way digest and are never returned by status/preview endpoints.
    """
    tailscale = state.get("tailscale", {})
    tailscale_activation = {
        "enabled": bool(tailscale.get("enabled")),
        "accept_routes": bool(tailscale.get("accept_routes")),
        "advertise_routes": sorted(tailscale.get("advertise_routes", [])),
        "exit_node": bool(tailscale.get("exit_node")),
    }
    parts = [generated.get(key) or "" for key in UNSAFE_GENERATOR_KEYS]
    parts.append(json.dumps(tailscale_activation, sort_keys=True, separators=(",", ":")))
    # Nebula's generated YAML points at credential files rather than embedding
    # PEM material, but Apply writes those files and restarts the daemon. Hash
    # the provider state as a nested digest so credential text never appears in
    # any status/preview value.
    nebula_state = json.dumps(state.get("nebula", {}), sort_keys=True, separators=(",", ":"))
    parts.append(hashlib.sha256(nebula_state.encode()).hexdigest())
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()
