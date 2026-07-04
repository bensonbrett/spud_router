# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Unit tests for backend/vpn_coexistence.py — the cross-provider
route-all/exit-node exclusivity check.
"""
import pytest

import backend.vpn_coexistence as vpn_coexistence


class TestRouteAllProviders:
    def test_empty_state_no_providers(self):
        assert vpn_coexistence.route_all_providers({}) == []

    def test_tailscale_disabled_not_counted_even_if_exit_node_set(self):
        state = {"tailscale": {"enabled": False, "exit_node": True}}
        assert vpn_coexistence.route_all_providers(state) == []

    def test_tailscale_enabled_without_exit_node_not_counted(self):
        state = {"tailscale": {"enabled": True, "exit_node": False}}
        assert vpn_coexistence.route_all_providers(state) == []

    def test_tailscale_enabled_and_exit_node_counted(self):
        state = {"tailscale": {"enabled": True, "exit_node": True}}
        assert vpn_coexistence.route_all_providers(state) == ["tailscale"]

    def test_wireguard_client_mode_with_route_all_peer_counted(self):
        state = {"wireguard": {"enabled": True, "mode": "client",
                                "peers": [{"allowed_ips": ["0.0.0.0/0"]}]}}
        assert vpn_coexistence.route_all_providers(state) == ["wireguard"]

    def test_wireguard_client_mode_without_route_all_not_counted(self):
        state = {"wireguard": {"enabled": True, "mode": "client",
                                "peers": [{"allowed_ips": ["10.100.0.0/24"]}]}}
        assert vpn_coexistence.route_all_providers(state) == []

    def test_wireguard_server_mode_never_counted_even_with_wide_allowed_ips(self):
        """0.0.0.0/0 in AllowedIPs only means route-all in client mode — in
        server mode it just means 'accept from any peer subnet', a normal
        hub configuration, not a default-route claim."""
        state = {"wireguard": {"enabled": True, "mode": "server",
                                "peers": [{"allowed_ips": ["0.0.0.0/0"]}]}}
        assert vpn_coexistence.route_all_providers(state) == []

    def test_wireguard_disabled_not_counted(self):
        state = {"wireguard": {"enabled": False, "mode": "client",
                                "peers": [{"allowed_ips": ["0.0.0.0/0"]}]}}
        assert vpn_coexistence.route_all_providers(state) == []


class TestValidateSingleRouteAll:
    def test_no_providers_ok(self):
        vpn_coexistence.validate_single_route_all({})  # must not raise

    def test_single_route_all_provider_ok(self):
        state = {"tailscale": {"enabled": True, "exit_node": True}}
        vpn_coexistence.validate_single_route_all(state)  # must not raise

    def test_tailscale_and_wireguard_both_route_all_rejected(self):
        state = {
            "tailscale": {"enabled": True, "exit_node": True},
            "wireguard": {"enabled": True, "mode": "client",
                           "peers": [{"allowed_ips": ["0.0.0.0/0"]}]},
        }
        with pytest.raises(ValueError, match="Only one VPN provider"):
            vpn_coexistence.validate_single_route_all(state)

    def test_multiple_route_all_providers_rejected(self, monkeypatch):
        # Simulate a further, not-yet-built provider (Nebula lands in a
        # later PR) to prove the exclusivity check generalizes beyond any
        # specific pair of providers.
        monkeypatch.setattr(vpn_coexistence, "ROUTE_ALL_CHECKS", [
            ("tailscale", lambda ts: bool(ts.get("exit_node"))),
            ("fake_provider", lambda fp: bool(fp.get("route_all"))),
        ])
        state = {
            "tailscale": {"enabled": True, "exit_node": True},
            "fake_provider": {"enabled": True, "route_all": True},
        }
        with pytest.raises(ValueError, match="Only one VPN provider"):
            vpn_coexistence.validate_single_route_all(state)
