# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Tests for generators/sysctl.py — split out of generators/iptables.py (#184)
so a sysctl-only change registers as connectivity-safe drift, separable
from firewall drift (see apply_core.activate_safe_subset()).
"""
from generators.sysctl import generate


class TestSysctlGenerator:
    def test_ip_forward_present(self, minimal_state):
        out = generate(minimal_state)
        assert "net.ipv4.ip_forward = 1" in out

    def test_ping_group_range_present(self, minimal_state):
        """
        #95 — the spud-router service runs unprivileged, so raw ICMP sockets
        (what a setuid `ping` normally needs) are denied. Setting
        net.ipv4.ping_group_range lets any GID in that range open an
        unprivileged SOCK_DGRAM ICMP ("ping socket") instead.
        """
        out = generate(minimal_state)
        assert "net.ipv4.ping_group_range = 0 2147483647" in out

    def test_output_is_unconditional_regardless_of_state(self, minimal_state, vlan_10):
        """Both settings are always-on today — no state field toggles them
        off, unlike every other generator."""
        minimal_state["vlans"] = [vlan_10]
        minimal_state["tailscale"]["enabled"] = True
        out = generate(minimal_state)
        assert "net.ipv4.ip_forward = 1" in out
        assert "net.ipv4.ping_group_range = 0 2147483647" in out

    def test_no_shell_syntax_just_key_value_lines(self, minimal_state):
        """Unlike the old embedded-in-iptables.sh heredoc, this is a plain
        sysctl.d file — no echo/cat/EOF shell syntax."""
        out = generate(minimal_state)
        assert "echo" not in out
        assert "EOF" not in out
        assert "/proc/sys" not in out
