# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Tests for generators/iptables.py

These tests verify the iptables script output — this is the most
security-critical code in the project. A bug here could leave a router
open to the internet or break inter-VLAN routing unexpectedly.
"""
import pytest
from generators.iptables import generate


class TestDefaultPolicies:
    def test_input_default_drop(self, minimal_state):
        out = generate(minimal_state)
        assert "$IPT -P INPUT DROP" in out

    def test_forward_default_drop(self, minimal_state):
        out = generate(minimal_state)
        assert "$IPT -P FORWARD DROP" in out

    def test_output_default_accept(self, minimal_state):
        out = generate(minimal_state)
        assert "$IPT -P OUTPUT ACCEPT" in out

    def test_flush_rules(self, minimal_state):
        out = generate(minimal_state)
        assert "$IPT -F" in out
        assert "$IPT -t nat -F" in out

    def test_loopback_accepted(self, minimal_state):
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i lo -j ACCEPT" in out

    def test_established_related_accepted(self, minimal_state):
        out = generate(minimal_state)
        assert "ESTABLISHED,RELATED" in out
        assert "ACCEPT" in out

    def test_ip_forwarding_enabled(self, minimal_state):
        out = generate(minimal_state)
        assert "echo 1 > /proc/sys/net/ipv4/ip_forward" in out

    def test_ip_forwarding_persisted(self, minimal_state):
        out = generate(minimal_state)
        assert "/etc/sysctl.d/99-spud-router.conf" in out
        assert "net.ipv4.ip_forward = 1" in out

    def test_iptables_rules_persisted(self, minimal_state):
        out = generate(minimal_state)
        assert "mkdir -p /etc/iptables" in out
        assert "iptables-save > /etc/iptables/rules.v4" in out

    def test_is_valid_bash_script(self, minimal_state):
        out = generate(minimal_state)
        assert out.startswith("#!/bin/bash")


class TestPingGroupRange:
    """
    #95 — the spud-router service runs unprivileged, so raw ICMP sockets
    (what a setuid `ping` normally needs) are denied. Setting
    net.ipv4.ping_group_range lets any GID in that range open an
    unprivileged SOCK_DGRAM ICMP ("ping socket") instead — no CAP_NET_RAW,
    no setuid bit, no reboot required since it's also applied live.
    """
    def test_ping_group_range_persisted(self, minimal_state):
        out = generate(minimal_state)
        assert "/etc/sysctl.d/99-spud-router.conf" in out
        assert "net.ipv4.ping_group_range = 0 2147483647" in out

    def test_ping_group_range_applied_live(self, minimal_state):
        out = generate(minimal_state)
        assert 'echo "0 2147483647" > /proc/sys/net/ipv4/ping_group_range' in out

    def test_ip_forward_still_persisted_alongside_ping_group_range(self, minimal_state):
        """Additive change — must not regress the existing ip_forward line."""
        out = generate(minimal_state)
        assert "net.ipv4.ip_forward = 1" in out
        assert "echo 1 > /proc/sys/net/ipv4/ip_forward" in out


class TestNat:
    def test_masquerade_on_wan(self, minimal_state):
        out = generate(minimal_state)
        assert "$IPT -t nat -A POSTROUTING -o eth1 -j MASQUERADE" in out

    def test_masquerade_uses_correct_wan_interface(self, minimal_state):
        minimal_state["router"]["wan_interface"] = "enp3s0"
        out = generate(minimal_state)
        assert "POSTROUTING -o enp3s0 -j MASQUERADE" in out


class TestPortForwarding:
    def test_enabled_forward_emits_dnat_and_forward_accept(self, minimal_state):
        minimal_state["port_forwards"] = [{
            "id": "abc123", "proto": "tcp", "wan_port": 8443,
            "lan_host": "192.168.10.50", "lan_port": 443,
            "description": "", "enabled": True,
        }]
        out = generate(minimal_state)
        assert "$IPT -t nat -A PREROUTING -i eth1 -p tcp --dport 8443 -j DNAT --to-destination 192.168.10.50:443" in out
        assert "$IPT -A FORWARD -i eth1 -p tcp -d 192.168.10.50 --dport 443 -j ACCEPT" in out

    def test_disabled_forward_emits_nothing(self, minimal_state):
        minimal_state["port_forwards"] = [{
            "id": "abc123", "proto": "tcp", "wan_port": 8443,
            "lan_host": "192.168.10.50", "lan_port": 443,
            "description": "", "enabled": False,
        }]
        out = generate(minimal_state)
        assert "8443" not in out
        assert "192.168.10.50" not in out

    def test_udp_forward(self, minimal_state):
        minimal_state["port_forwards"] = [{
            "id": "abc123", "proto": "udp", "wan_port": 51820,
            "lan_host": "192.168.10.5", "lan_port": 51820,
            "description": "", "enabled": True,
        }]
        out = generate(minimal_state)
        assert "$IPT -t nat -A PREROUTING -i eth1 -p udp --dport 51820 -j DNAT --to-destination 192.168.10.5:51820" in out
        assert "$IPT -A FORWARD -i eth1 -p udp -d 192.168.10.5 --dport 51820 -j ACCEPT" in out

    def test_description_sanitized_in_comment(self, minimal_state):
        minimal_state["port_forwards"] = [{
            "id": "abc123", "proto": "tcp", "wan_port": 80,
            "lan_host": "192.168.10.1", "lan_port": 80,
            "description": "NAS\nadmin\rpanel", "enabled": True,
        }]
        out = generate(minimal_state)
        assert "# NAS admin panel" in out
        assert "NAS\nadmin" not in out
        assert "admin\rpanel" not in out

    def test_forward_accept_before_intervlan_section(self, minimal_state):
        minimal_state["port_forwards"] = [{
            "id": "abc123", "proto": "tcp", "wan_port": 8443,
            "lan_host": "192.168.10.50", "lan_port": 443,
            "description": "", "enabled": True,
        }]
        out = generate(minimal_state)
        forward_line = "$IPT -A FORWARD -i eth1 -p tcp -d 192.168.10.50 --dport 443 -j ACCEPT"
        assert forward_line in out
        assert out.index(forward_line) < out.index("# ── Inter-VLAN forwarding")

    def test_multiple_forwards_all_emitted(self, minimal_state):
        minimal_state["port_forwards"] = [
            {"id": "a", "proto": "tcp", "wan_port": 80, "lan_host": "192.168.10.1",
             "lan_port": 80, "description": "", "enabled": True},
            {"id": "b", "proto": "udp", "wan_port": 53, "lan_host": "192.168.10.2",
             "lan_port": 53, "description": "", "enabled": True},
        ]
        out = generate(minimal_state)
        assert "--dport 80 -j DNAT --to-destination 192.168.10.1:80" in out
        assert "--dport 53 -j DNAT --to-destination 192.168.10.2:53" in out

    def test_no_port_forwards_key_does_not_crash(self, minimal_state):
        # minimal_state fixture doesn't include port_forwards — generate()
        # must tolerate its absence via state.get(..., []).
        assert "port_forwards" not in minimal_state
        out = generate(minimal_state)
        assert out.startswith("#!/bin/bash")


class TestBuiltinLanRules:
    def test_dns_open_on_vlan(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth0.10 -p udp --dport 53 -j ACCEPT" in out
        assert "$IPT -A INPUT -i eth0.10 -p tcp --dport 53 -j ACCEPT" in out

    def test_dhcp_open_on_vlan(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth0.10 -p udp --dport 67 -j ACCEPT" in out

    def test_builtin_rules_for_all_vlans(self, minimal_state, vlan_10, vlan_20):
        minimal_state["vlans"] = [vlan_10, vlan_20]
        out = generate(minimal_state)
        assert "eth0.10 -p udp --dport 53" in out
        assert "eth0.20 -p udp --dport 53" in out


class TestVlanForwarding:
    def test_vlan_to_wan_always_allowed(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth0.10 -o eth1 -j ACCEPT" in out

    def test_non_isolated_vlans_meshed_in_auto_mode(self, minimal_state, vlan_10, vlan_20):
        """vlan_10 is not isolated; vlan_20 is. Only non-isolated pairs are meshed."""
        vlan_20["isolate"] = False
        minimal_state["vlans"] = [vlan_10, vlan_20]
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth0.10 -o eth0.20 -j ACCEPT" in out
        assert "$IPT -A FORWARD -i eth0.20 -o eth0.10 -j ACCEPT" in out

    def test_isolated_vlan_not_meshed(self, minimal_state, vlan_10, vlan_20):
        """vlan_20 is isolated — no FORWARD rules between it and vlan_10."""
        vlan_20["isolate"] = True
        minimal_state["vlans"] = [vlan_10, vlan_20]
        out = generate(minimal_state)
        assert "eth0.10 -o eth0.20" not in out
        assert "eth0.20 -o eth0.10" not in out

    def test_isolated_vlan_still_reaches_wan(self, minimal_state, vlan_20):
        """Isolated VLANs are still NAT-ted to WAN."""
        vlan_20["isolate"] = True
        minimal_state["vlans"] = [vlan_20]
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth0.20 -o eth1 -j ACCEPT" in out


class TestExplicitIntervlanMode:
    def test_switching_to_explicit_mode(self, minimal_state, vlan_10, vlan_20):
        """Adding one inter-VLAN rule switches to explicit mode (default deny)."""
        vlan_10["isolate"] = False
        vlan_20["isolate"] = False
        minimal_state["vlans"] = [vlan_10, vlan_20]
        minimal_state["fw_intervlan"] = [{
            "id": "aabb",
            "from_vlan": 10,
            "to_vlan": 20,
            "proto": "tcp",
            "port": 443,
            "action": "accept",
            "description": "Trusted → IoT HTTPS",
        }]
        out = generate(minimal_state)
        # The specific rule should be present
        assert "-i eth0.10 -o eth0.20 -p tcp --dport 443 -j ACCEPT" in out
        # The reverse direction should NOT be automatically added
        assert "-i eth0.20 -o eth0.10" not in out

    def test_explicit_drop_rule(self, minimal_state, vlan_10, vlan_20):
        minimal_state["vlans"] = [vlan_10, vlan_20]
        minimal_state["fw_intervlan"] = [{
            "id": "ccdd",
            "from_vlan": 20,
            "to_vlan": 10,
            "proto": "any",
            "port": None,
            "action": "drop",
            "description": "Block IoT → Trusted",
        }]
        out = generate(minimal_state)
        assert "-i eth0.20 -o eth0.10 -j DROP" in out

    def test_explicit_any_proto_no_flags(self, minimal_state, vlan_10, vlan_20):
        """proto=any should not add -p or --dport flags."""
        minimal_state["vlans"] = [vlan_10, vlan_20]
        minimal_state["fw_intervlan"] = [{
            "id": "eeff",
            "from_vlan": 10,
            "to_vlan": 20,
            "proto": "any",
            "port": None,
            "action": "accept",
            "description": "",
        }]
        out = generate(minimal_state)
        rule_line = [l for l in out.split("\n") if "eth0.10 -o eth0.20" in l][0]
        assert "-p " not in rule_line
        assert "--dport" not in rule_line

    def test_vlan_zero_means_all_vlans(self, minimal_state, vlan_10, vlan_20):
        """from_vlan=0 should expand to all non-isolated VLANs."""
        vlan_10["isolate"] = False
        vlan_20["isolate"] = False
        minimal_state["vlans"] = [vlan_10, vlan_20]
        minimal_state["fw_intervlan"] = [{
            "id": "0000",
            "from_vlan": 0,
            "to_vlan": 0,
            "proto": "tcp",
            "port": 80,
            "action": "accept",
            "description": "",
        }]
        out = generate(minimal_state)
        assert "-i eth0.10 -o eth0.20 -p tcp --dport 80 -j ACCEPT" in out
        assert "-i eth0.20 -o eth0.10 -p tcp --dport 80 -j ACCEPT" in out


class TestUserInboundRules:
    def test_inbound_rule_on_specific_vlan(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["fw_inbound"] = [{
            "id": "1122",
            "vlan_id": 10,
            "proto": "tcp",
            "port": 22,
            "action": "accept",
            "description": "SSH",
        }]
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth0.10 -p tcp --dport 22 -j ACCEPT" in out

    def test_inbound_rule_on_all_vlans(self, minimal_state, vlan_10, vlan_20):
        minimal_state["vlans"] = [vlan_10, vlan_20]
        minimal_state["fw_inbound"] = [{
            "id": "3344",
            "vlan_id": 0,
            "proto": "tcp",
            "port": 8080,
            "action": "accept",
            "description": "Web UI",
        }]
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth0.10 -p tcp --dport 8080 -j ACCEPT" in out
        assert "$IPT -A INPUT -i eth0.20 -p tcp --dport 8080 -j ACCEPT" in out

    def test_inbound_drop_rule(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["fw_inbound"] = [{
            "id": "5566",
            "vlan_id": 10,
            "proto": "tcp",
            "port": 23,
            "action": "drop",
            "description": "Block Telnet",
        }]
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth0.10 -p tcp --dport 23 -j DROP" in out

    def test_unknown_vlan_id_produces_no_rule(self, minimal_state, vlan_10):
        """A rule referencing a non-existent VLAN should produce no iptables rule."""
        minimal_state["vlans"] = [vlan_10]
        minimal_state["fw_inbound"] = [{
            "id": "7788",
            "vlan_id": 99,  # doesn't exist
            "proto": "tcp",
            "port": 22,
            "action": "accept",
            "description": "",
        }]
        out = generate(minimal_state)
        assert "eth0.99" not in out


class TestTailscale:
    def test_tailscale_rules_when_enabled(self, minimal_state):
        minimal_state["tailscale"]["enabled"] = True
        out = generate(minimal_state)
        assert "$IPT -A INPUT   -i tailscale0 -j ACCEPT" in out
        assert "$IPT -A FORWARD -i tailscale0 -j ACCEPT" in out
        assert "$IPT -A FORWARD -o tailscale0 -j ACCEPT" in out

    def test_no_tailscale_rules_when_disabled(self, minimal_state):
        minimal_state["tailscale"]["enabled"] = False
        out = generate(minimal_state)
        assert "tailscale0" not in out

    def test_tailscale_subnet_snat_when_enabled(self, minimal_state):
        """Without this, a LAN client's traffic forwarded onto the tailnet
        keeps its real LAN source IP, so a remote subnet router's ACL/routing
        has no reason to accept or return it — see tailscale-subnet-snat-plan."""
        minimal_state["tailscale"]["enabled"] = True
        out = generate(minimal_state)
        assert "$IPT -t nat -A POSTROUTING -o tailscale0 -j MASQUERADE" in out

    def test_no_tailscale_subnet_snat_when_disabled(self, minimal_state):
        minimal_state["tailscale"]["enabled"] = False
        out = generate(minimal_state)
        assert "-o tailscale0 -j MASQUERADE" not in out

    def test_wan_masquerade_unaffected_by_tailscale(self, minimal_state):
        minimal_state["tailscale"]["enabled"] = True
        out = generate(minimal_state)
        assert "$IPT -t nat -A POSTROUTING -o eth1 -j MASQUERADE" in out


class TestVpnProviderGeneralization:
    """
    The INPUT/FORWARD/MASQUERADE treatment for VPN interfaces is generated
    from VPN_PROVIDER_INTERFACES (state key -> OS interface name), not
    hardcoded to Tailscale — future providers (WireGuard's wg0, Nebula's
    nebula1) register by adding one entry there and stack additively with
    whatever else is enabled. These tests simulate a second provider via
    monkeypatch to prove the loop genuinely generalizes, without depending
    on WireGuard/Nebula's own state shape (added in later PRs).
    """
    def test_multiple_providers_stack_additively(self, minimal_state, monkeypatch):
        from generators import iptables as iptables_module
        monkeypatch.setattr(iptables_module, "VPN_PROVIDER_INTERFACES", {
            "tailscale": "tailscale0",
            "fake_provider": "fake0",
        })
        minimal_state["tailscale"]["enabled"] = True
        minimal_state["fake_provider"] = {"enabled": True}

        out = generate(minimal_state)
        for ifname in ("tailscale0", "fake0"):
            assert f"$IPT -A INPUT   -i {ifname} -j ACCEPT" in out
            assert f"$IPT -A FORWARD -i {ifname} -j ACCEPT" in out
            assert f"$IPT -A FORWARD -o {ifname} -j ACCEPT" in out
            assert f"$IPT -t nat -A POSTROUTING -o {ifname} -j MASQUERADE" in out

    def test_second_provider_disabled_only_first_emitted(self, minimal_state, monkeypatch):
        from generators import iptables as iptables_module
        monkeypatch.setattr(iptables_module, "VPN_PROVIDER_INTERFACES", {
            "tailscale": "tailscale0",
            "fake_provider": "fake0",
        })
        minimal_state["tailscale"]["enabled"] = True
        minimal_state["fake_provider"] = {"enabled": False}

        out = generate(minimal_state)
        assert "tailscale0" in out
        assert "fake0" not in out

    def test_unknown_provider_key_missing_from_state_is_safe(self, minimal_state, monkeypatch):
        """A registered provider whose state section doesn't exist yet
        (e.g. mid-rollout) must not raise — state.get(key, {}) covers it."""
        from generators import iptables as iptables_module
        monkeypatch.setattr(iptables_module, "VPN_PROVIDER_INTERFACES", {
            "tailscale": "tailscale0",
            "not_in_state_yet": "wg0",
        })
        minimal_state["tailscale"]["enabled"] = False
        out = generate(minimal_state)  # must not raise
        assert "wg0" not in out

    def test_wireguard_registered_and_stacks_with_tailscale(self, minimal_state):
        """WireGuard is a real (not simulated) entry in
        VPN_PROVIDER_INTERFACES — both providers enabled at once must
        stack additively, same as the generic test above but proving the
        actual production registration, not a monkeypatched stand-in."""
        minimal_state["tailscale"]["enabled"] = True
        minimal_state["wireguard"] = {"enabled": True, "mode": "server", "listen_port": 51820}
        out = generate(minimal_state)
        for ifname in ("tailscale0", "wg0"):
            assert f"$IPT -A INPUT   -i {ifname} -j ACCEPT" in out
            assert f"$IPT -t nat -A POSTROUTING -o {ifname} -j MASQUERADE" in out


class TestWireguardFirewall:
    def test_server_mode_opens_listen_port_on_wan(self, minimal_state):
        minimal_state["wireguard"] = {"enabled": True, "mode": "server", "listen_port": 51820}
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth1 -p udp --dport 51820 -j ACCEPT" in out

    def test_custom_listen_port_used(self, minimal_state):
        minimal_state["wireguard"] = {"enabled": True, "mode": "server", "listen_port": 12345}
        out = generate(minimal_state)
        assert "--dport 12345 -j ACCEPT" in out
        assert "--dport 51820" not in out

    def test_client_mode_does_not_open_listen_port(self, minimal_state):
        minimal_state["wireguard"] = {"enabled": True, "mode": "client", "listen_port": 51820}
        out = generate(minimal_state)
        assert "--dport 51820 -j ACCEPT" not in out

    def test_disabled_does_not_open_listen_port(self, minimal_state):
        minimal_state["wireguard"] = {"enabled": False, "mode": "server", "listen_port": 51820}
        out = generate(minimal_state)
        assert "--dport 51820" not in out

    def test_missing_wireguard_key_is_safe(self, minimal_state):
        out = generate(minimal_state)  # must not raise
        assert "51820" not in out


_NEBULA_CERT = "-----BEGIN NEBULA CERTIFICATE-----\nabc\n-----END NEBULA CERTIFICATE-----\n"
_NEBULA_KEY  = "-----BEGIN NEBULA ED25519 PRIVATE KEY-----\nabc\n-----END NEBULA ED25519 PRIVATE KEY-----\n"


class TestNebulaFirewall:
    def test_opens_listen_port_with_complete_credentials(self, minimal_state):
        minimal_state["nebula"] = {
            "enabled": True, "listen_port": 4242,
            "cert_pem": _NEBULA_CERT, "key_pem": _NEBULA_KEY, "ca_pem": _NEBULA_CERT,
        }
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth1 -p udp --dport 4242 -j ACCEPT" in out

    def test_custom_listen_port_used(self, minimal_state):
        minimal_state["nebula"] = {
            "enabled": True, "listen_port": 6000,
            "cert_pem": _NEBULA_CERT, "key_pem": _NEBULA_KEY, "ca_pem": _NEBULA_CERT,
        }
        out = generate(minimal_state)
        assert "--dport 6000 -j ACCEPT" in out
        assert "--dport 4242" not in out

    def test_incomplete_credentials_does_not_open_port(self, minimal_state):
        minimal_state["nebula"] = {"enabled": True, "listen_port": 4242, "cert_pem": "", "key_pem": "", "ca_pem": ""}
        out = generate(minimal_state)
        assert "--dport 4242" not in out

    def test_disabled_does_not_open_listen_port(self, minimal_state):
        minimal_state["nebula"] = {
            "enabled": False, "listen_port": 4242,
            "cert_pem": _NEBULA_CERT, "key_pem": _NEBULA_KEY, "ca_pem": _NEBULA_CERT,
        }
        out = generate(minimal_state)
        assert "--dport 4242" not in out

    def test_missing_nebula_key_is_safe(self, minimal_state):
        out = generate(minimal_state)  # must not raise
        assert "4242" not in out

    def test_registered_and_stacks_with_tailscale_and_wireguard(self, minimal_state):
        """nebula1 is a real (not simulated) entry in VPN_PROVIDER_INTERFACES —
        all three providers enabled at once must stack additively."""
        minimal_state["tailscale"]["enabled"] = True
        minimal_state["wireguard"] = {"enabled": True, "mode": "server", "listen_port": 51820}
        minimal_state["nebula"] = {"enabled": True}
        out = generate(minimal_state)
        for ifname in ("tailscale0", "wg0", "nebula1"):
            assert f"$IPT -A INPUT   -i {ifname} -j ACCEPT" in out
            assert f"$IPT -t nat -A POSTROUTING -o {ifname} -j MASQUERADE" in out


class TestManagementInterface:
    def test_mgmt_opens_ssh_and_webui(self, minimal_state):
        minimal_state["router"]["mgmt_enabled"]  = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth0 -p tcp --dport 22   -j ACCEPT" in out
        assert "$IPT -A INPUT -i eth0 -p tcp --dport 8080 -j ACCEPT" in out

    def test_mgmt_opens_dns_and_dhcp(self, minimal_state):
        minimal_state["router"]["mgmt_enabled"]  = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth0 -p udp --dport 53 -j ACCEPT" in out
        assert "$IPT -A INPUT -i eth0 -p udp --dport 67 -j ACCEPT" in out

    def test_mgmt_allows_forward_to_wan(self, minimal_state):
        minimal_state["router"]["mgmt_enabled"]  = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth0 -o eth1 -j ACCEPT" in out

    def test_no_mgmt_rules_when_disabled(self, minimal_state):
        minimal_state["router"]["mgmt_enabled"] = False
        out = generate(minimal_state)
        # No explicit INPUT rules for eth0 (other than built-in loop/established)
        assert "-i eth0 -p tcp --dport 22" not in out
        assert "-i eth0 -p tcp --dport 8080" not in out

    def test_mgmt_ping_disabled_emits_drop_before_conntrack(self, minimal_state):
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        minimal_state["router"]["mgmt_ip"] = "192.168.1.1"
        minimal_state["router"]["mgmt_icmp_echo"] = False
        out = generate(minimal_state)

        drop = "$IPT -A INPUT -d 192.168.1.1 -p icmp --icmp-type echo-request -j DROP"
        established = "$IPT -A INPUT  -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
        assert drop in out
        assert out.index(drop) < out.index(established)

    def test_mgmt_ping_enabled_emits_accept_before_conntrack(self, minimal_state):
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        minimal_state["router"]["mgmt_ip"] = "192.168.1.1"
        minimal_state["router"]["mgmt_icmp_echo"] = True
        out = generate(minimal_state)

        accept = "$IPT -A INPUT -d 192.168.1.1 -p icmp --icmp-type echo-request -j ACCEPT"
        established = "$IPT -A INPUT  -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
        assert accept in out
        assert out.count(accept) == 1
        assert out.index(accept) < out.index(established)


class TestOutboundFirewall:
    def test_default_allow_no_rules_matches_prior_behavior(self, minimal_state, vlan_10):
        """No fw_outbound state at all (old/imported configs) must reproduce
        today's always-allow egress exactly — no regression."""
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth0.10 -o eth1 -j ACCEPT" in out

    def test_explicit_allow_default_no_rules(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["fw_outbound"] = []
        minimal_state["fw_outbound_default"] = "allow"
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth0.10 -o eth1 -j ACCEPT" in out

    def test_deny_default_no_rules_drops_every_lan_vlan(self, minimal_state, vlan_10, vlan_20):
        minimal_state["vlans"] = [vlan_10, vlan_20]
        minimal_state["fw_outbound"] = []
        minimal_state["fw_outbound_default"] = "deny"
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth0.10 -o eth1 -j DROP" in out
        assert "$IPT -A FORWARD -i eth0.20 -o eth1 -j DROP" in out
        assert "-i eth0.10 -o eth1 -j ACCEPT" not in out
        assert "-i eth0.20 -o eth1 -j ACCEPT" not in out

    def test_drop_rule_blocks_specific_vlan_others_unaffected(self, minimal_state, vlan_10, vlan_20):
        minimal_state["vlans"] = [vlan_10, vlan_20]
        minimal_state["fw_outbound"] = [{
            "id": "aabb", "vlan_id": 20, "dest": "", "proto": "any", "port": None,
            "action": "drop", "description": "Block IoT internet",
        }]
        minimal_state["fw_outbound_default"] = "allow"
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth0.20 -o eth1 -j DROP" in out
        # vlan_20's own fallback (allow) still follows, but the drop rule comes first (first-match)
        lines = [l for l in out.split("\n") if "eth0.20 -o eth1" in l]
        assert lines[0].endswith("-j DROP  # Block IoT internet")
        # vlan_10 is unaffected — only the allow fallback, no drop
        assert "$IPT -A FORWARD -i eth0.10 -o eth1 -j ACCEPT" in out
        assert "eth0.10 -o eth1 -j DROP" not in out

    def test_dest_and_proto_port_flags(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["fw_outbound"] = [{
            "id": "ccdd", "vlan_id": 10, "dest": "8.8.8.8", "proto": "udp", "port": 53,
            "action": "accept", "description": "",
        }]
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth0.10 -o eth1 -d 8.8.8.8 -p udp --dport 53 -j ACCEPT" in out

    def test_dest_cidr_without_proto(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["fw_outbound"] = [{
            "id": "ee01", "vlan_id": 10, "dest": "10.0.0.0/8", "proto": "any", "port": None,
            "action": "drop", "description": "",
        }]
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth0.10 -o eth1 -d 10.0.0.0/8 -j DROP" in out

    def test_vlan_zero_applies_to_all_lan_vlans(self, minimal_state, vlan_10, vlan_20):
        minimal_state["vlans"] = [vlan_10, vlan_20]
        minimal_state["fw_outbound"] = [{
            "id": "ff02", "vlan_id": 0, "dest": "", "proto": "tcp", "port": 443,
            "action": "accept", "description": "",
        }]
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth0.10 -o eth1 -p tcp --dport 443 -j ACCEPT" in out
        assert "$IPT -A FORWARD -i eth0.20 -o eth1 -p tcp --dport 443 -j ACCEPT" in out

    def test_wan_vlan_and_mgmt_egress_unaffected(self, minimal_state, vlan_10):
        """A WAN VLAN (no ip_address) never gets egress rules; mgmt egress
        stays unconditionally allowed regardless of fw_outbound state."""
        wan_vlan = {
            "vlan_id": 2, "name": "WAN", "interface": "eth0",
            "ip_address": "", "prefix_len": 0, "dhcp_enabled": False,
            "dhcp_start": "", "dhcp_end": "", "dhcp_lease": "12h", "isolate": False,
        }
        minimal_state["vlans"] = [wan_vlan, vlan_10]
        minimal_state["fw_outbound_default"] = "deny"
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth1mgmt"
        out = generate(minimal_state)
        assert "eth0.2" not in out
        assert "$IPT -A FORWARD -i eth1mgmt -o eth1 -j ACCEPT" in out

    def test_rule_order_preserved_fallback_last(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["fw_outbound"] = [
            {"id": "r1", "vlan_id": 10, "dest": "", "proto": "tcp", "port": 443, "action": "accept", "description": ""},
            {"id": "r2", "vlan_id": 10, "dest": "", "proto": "tcp", "port": 80, "action": "drop", "description": ""},
        ]
        minimal_state["fw_outbound_default"] = "deny"
        out = generate(minimal_state)
        lines = [l for l in out.split("\n") if "eth0.10 -o eth1" in l]
        assert lines[0].endswith("--dport 443 -j ACCEPT")
        assert lines[1].endswith("--dport 80 -j DROP")
        assert lines[2].endswith("-j DROP")
        assert "--dport" not in lines[2]


class TestIcmpFirewall:
    def test_icmp_inbound_rule_with_named_type(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["fw_inbound"] = [{
            "id": "i001", "vlan_id": 10, "proto": "icmp",
            "icmp_type": "echo-request", "icmp_code": None,
            "action": "accept", "description": "Allow ping",
        }]
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth0.10 -p icmp --icmp-type echo-request -j ACCEPT  # Allow ping" in out

    def test_icmp_rule_with_type_and_code(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["fw_inbound"] = [{
            "id": "i002", "vlan_id": 10, "proto": "icmp",
            "icmp_type": "destination-unreachable", "icmp_code": 3,
            "action": "accept", "description": "",
        }]
        out = generate(minimal_state)
        assert "-p icmp --icmp-type destination-unreachable/3 -j ACCEPT" in out

    def test_icmp_numeric_type(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["fw_inbound"] = [{
            "id": "i003", "vlan_id": 10, "proto": "icmp",
            "icmp_type": "8", "icmp_code": None,
            "action": "accept", "description": "",
        }]
        out = generate(minimal_state)
        assert "-p icmp --icmp-type 8 -j ACCEPT" in out

    def test_icmp_no_type_emits_bare_proto(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["fw_inbound"] = [{
            "id": "i004", "vlan_id": 10, "proto": "icmp",
            "icmp_type": None, "icmp_code": None,
            "action": "drop", "description": "",
        }]
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth0.10 -p icmp -j DROP" in out

    def test_default_blocks_ping_no_echo_accept(self, minimal_state, vlan_10):
        """Secure by default: no icmp_echo toggle set anywhere → explicit echo-request DROP."""
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        assert "$IPT -A INPUT -d 192.168.10.1 -p icmp --icmp-type echo-request -j DROP" in out

    def test_vlan_icmp_echo_enabled_adds_accept(self, minimal_state, vlan_10):
        vlan_10["icmp_echo"] = True
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        accept = "$IPT -A INPUT -d 192.168.10.1 -p icmp --icmp-type echo-request -j ACCEPT"
        established = "$IPT -A INPUT  -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
        assert accept in out
        assert out.index(accept) < out.index(established)

    def test_vlan_icmp_echo_disabled_by_default(self, minimal_state, vlan_10, vlan_20):
        """Disabled VLANs must get an explicit DROP before conntrack."""
        vlan_10["icmp_echo"] = True
        vlan_20["icmp_echo"] = False
        minimal_state["vlans"] = [vlan_10, vlan_20]
        out = generate(minimal_state)
        accept = "-d 192.168.10.1 -p icmp --icmp-type echo-request -j ACCEPT"
        drop = "-d 192.168.20.1 -p icmp --icmp-type echo-request -j DROP"
        established = "$IPT -A INPUT  -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
        assert accept in out
        assert drop in out
        assert out.index(accept) < out.index(established)
        assert out.index(drop) < out.index(established)

    def test_mgmt_icmp_echo_enabled(self, minimal_state):
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        minimal_state["router"]["mgmt_ip"] = "192.168.1.1"
        minimal_state["router"]["mgmt_icmp_echo"] = True
        out = generate(minimal_state)
        assert "$IPT -A INPUT -d 192.168.1.1 -p icmp --icmp-type echo-request -j ACCEPT" in out

    def test_mgmt_icmp_echo_default_blocked(self, minimal_state):
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        minimal_state["router"]["mgmt_ip"] = "192.168.1.1"
        out = generate(minimal_state)
        assert "-d 192.168.1.1 -p icmp --icmp-type echo-request -j DROP" in out

    def test_icmp_echo_ip_with_cidr_suffix_uses_bare_ip(self, minimal_state, vlan_10):
        """mgmt_ip/vlan ip_address stored with a CIDR suffix must still
        produce a bare -d <ip> — iptables doesn't want the /prefix."""
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        minimal_state["router"]["mgmt_ip"] = "192.168.1.1/24"
        vlan_10["ip_address"] = "192.168.10.1/24"
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        assert "$IPT -A INPUT -d 192.168.1.1 -p icmp --icmp-type echo-request" in out
        assert "$IPT -A INPUT -d 192.168.10.1 -p icmp --icmp-type echo-request" in out
        assert "192.168.1.1/24" not in out
        assert "192.168.10.1/24" not in out

    def test_no_mgmt_ping_rule_when_mgmt_disabled(self, minimal_state):
        minimal_state["router"]["mgmt_enabled"] = False
        out = generate(minimal_state)
        assert "192.168.1.1" not in out
        assert "Management IP ping policy" not in out

    # ── #170: blocked ping must drop in raw PREROUTING so tailscale's
    # ts-input INPUT jump can't accept a tailnet ping ahead of our rule ──
    def test_raw_prerouting_flushed(self, minimal_state):
        assert "$IPT -t raw -F PREROUTING" in generate(minimal_state)

    def test_mgmt_ping_disabled_drops_in_raw_prerouting(self, minimal_state):
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        minimal_state["router"]["mgmt_ip"] = "192.168.1.1"
        minimal_state["router"]["mgmt_icmp_echo"] = False
        out = generate(minimal_state)
        assert "$IPT -t raw -A PREROUTING -d 192.168.1.1 -p icmp --icmp-type echo-request -j DROP" in out

    def test_vlan_ping_disabled_drops_in_raw_prerouting(self, minimal_state, vlan_10):
        vlan_10["icmp_echo"] = False
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        assert "$IPT -t raw -A PREROUTING -d 192.168.10.1 -p icmp --icmp-type echo-request -j DROP" in out

    def test_ping_enabled_emits_no_raw_drop(self, minimal_state, vlan_10):
        """When ping is allowed, there must be no raw PREROUTING drop for that IP."""
        vlan_10["icmp_echo"] = True
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        assert "$IPT -A INPUT -d 192.168.10.1 -p icmp --icmp-type echo-request -j ACCEPT" in out
        assert "-t raw -A PREROUTING -d 192.168.10.1" not in out

    def test_icmp_outbound_rule(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["fw_outbound"] = [{
            "id": "o001", "vlan_id": 10, "dest": "", "proto": "icmp",
            "icmp_type": "echo-request", "icmp_code": None,
            "action": "accept", "description": "",
        }]
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth0.10 -o eth1 -p icmp --icmp-type echo-request -j ACCEPT" in out

    def test_icmp_intervlan_rule(self, minimal_state, vlan_10, vlan_20):
        minimal_state["vlans"] = [vlan_10, vlan_20]
        minimal_state["fw_intervlan"] = [{
            "id": "iv001", "from_vlan": 10, "to_vlan": 20, "proto": "icmp",
            "icmp_type": "echo-request", "icmp_code": None,
            "action": "accept", "description": "",
        }]
        out = generate(minimal_state)
        assert "-i eth0.10 -o eth0.20 -p icmp --icmp-type echo-request -j ACCEPT" in out


class TestSnmpFirewall:
    def test_snmp_disabled_no_udp_161_rule(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["snmp"] = {"enabled": False}
        out = generate(minimal_state)
        assert "--dport 161" not in out

    def test_missing_snmp_key_no_udp_161_rule(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        assert "--dport 161" not in out

    def test_snmp_enabled_opens_udp_161_on_lan_vlans(self, minimal_state, vlan_10, vlan_20):
        minimal_state["vlans"] = [vlan_10, vlan_20]
        minimal_state["snmp"] = {"enabled": True}
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth0.10 -p udp --dport 161 -j ACCEPT" in out
        assert "$IPT -A INPUT -i eth0.20 -p udp --dport 161 -j ACCEPT" in out

    def test_snmp_enabled_opens_udp_161_on_mgmt_interface(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["snmp"] = {"enabled": True}
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth0 -p udp --dport 161 -j ACCEPT" in out

    def test_snmp_enabled_no_mgmt_no_mgmt_rule(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["snmp"] = {"enabled": True}
        minimal_state["router"]["mgmt_enabled"] = False
        out = generate(minimal_state)
        assert out.count("--dport 161") == 1  # only the vlan_10 rule

    def test_snmp_skips_wan_vlan(self, minimal_state, vlan_10):
        wan_vlan = {
            "vlan_id": 2, "name": "WAN", "interface": "eth0",
            "ip_address": "", "prefix_len": 0, "dhcp_enabled": False,
            "dhcp_start": "", "dhcp_end": "", "dhcp_lease": "12h", "isolate": False,
        }
        minimal_state["vlans"] = [wan_vlan, vlan_10]
        minimal_state["snmp"] = {"enabled": True}
        out = generate(minimal_state)
        assert "eth0.2" not in out


class TestDohBlockWanDns:
    def test_block_off_by_default(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["router"]["wan_dns_mode"] = "doh"
        # block_wan_dns absent → defaults False
        out = generate(minimal_state)
        assert "-j REJECT" not in out

    def test_block_requires_doh_mode(self, minimal_state, vlan_10):
        """Never block :53 outside doh mode — would cause a DNS outage."""
        minimal_state["vlans"] = [vlan_10]
        minimal_state["router"]["wan_dns_mode"] = "manual"
        minimal_state["router"]["block_wan_dns"] = True
        out = generate(minimal_state)
        assert "-j REJECT" not in out

    def test_block_enabled_in_doh_mode(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["router"]["wan_dns_mode"] = "doh"
        minimal_state["router"]["block_wan_dns"] = True
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth0.10 -o eth1 -p udp --dport 53 -j REJECT" in out
        assert "$IPT -A FORWARD -i eth0.10 -o eth1 -p tcp --dport 53 -j REJECT" in out
        assert "$IPT -A OUTPUT -o eth1 -p udp --dport 53 -j REJECT" in out
        assert "$IPT -A OUTPUT -o eth1 -p tcp --dport 53 -j REJECT" in out

    def test_block_rule_precedes_user_outbound_allow(self, minimal_state, vlan_10):
        """A permissive user fw_out ALLOW rule must not bypass the DNS block —
        the REJECT lines must come first (iptables is first-match)."""
        minimal_state["vlans"] = [vlan_10]
        minimal_state["router"]["wan_dns_mode"] = "doh"
        minimal_state["router"]["block_wan_dns"] = True
        minimal_state["fw_outbound"] = [{
            "id": "r1", "vlan_id": 10, "dest": "", "proto": "any", "port": None,
            "action": "accept", "description": "",
        }]
        out = generate(minimal_state)
        lines = [l for l in out.split("\n") if "eth0.10 -o eth1" in l]
        assert lines[0].endswith("--dport 53 -j REJECT")
        assert lines[1].endswith("--dport 53 -j REJECT")

    def test_all_lan_vlans_blocked(self, minimal_state, vlan_10, vlan_20):
        minimal_state["vlans"] = [vlan_10, vlan_20]
        minimal_state["router"]["wan_dns_mode"] = "doh"
        minimal_state["router"]["block_wan_dns"] = True
        out = generate(minimal_state)
        assert "-i eth0.10 -o eth1 -p udp --dport 53 -j REJECT" in out
        assert "-i eth0.20 -o eth1 -p udp --dport 53 -j REJECT" in out

    def test_wan_masquerade_and_dns_accept_unaffected(self, minimal_state, vlan_10):
        """Built-in LAN-facing DNS (port 53 on the VLAN's own INPUT, for
        clients asking the router itself) must stay untouched — only WAN-
        bound FORWARD/OUTPUT traffic is blocked."""
        minimal_state["vlans"] = [vlan_10]
        minimal_state["router"]["wan_dns_mode"] = "doh"
        minimal_state["router"]["block_wan_dns"] = True
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth0.10 -p udp --dport 53 -j ACCEPT" in out
        assert "$IPT -A INPUT -i eth0.10 -p tcp --dport 53 -j ACCEPT" in out


class TestDohBootstrapException:
    """The narrow OUTPUT exception (issue #127) that lets the router's own
    dnsproxy resolve a *custom* DoH upstream hostname even with
    block_wan_dns active — built-in providers are IP-literal and never
    need it, and LAN clients' FORWARD :53 block must stay fully intact."""

    def test_builtin_provider_emits_no_bootstrap_exception(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["router"]["wan_dns_mode"] = "doh"
        minimal_state["router"]["block_wan_dns"] = True
        minimal_state["router"]["doh_provider"] = "cloudflare"
        out = generate(minimal_state)
        assert "-d 1.1.1.1 -j ACCEPT" not in out

    def test_custom_hostname_emits_bootstrap_exception_before_reject(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["router"]["wan_dns_mode"] = "doh"
        minimal_state["router"]["block_wan_dns"] = True
        minimal_state["router"]["doh_provider"] = "custom"
        minimal_state["router"]["doh_custom_url"] = "https://doh.example.com/dns-query"
        out = generate(minimal_state)
        assert "$IPT -A OUTPUT -o eth1 -p udp --dport 53 -d 1.1.1.1 -j ACCEPT" in out
        assert "$IPT -A OUTPUT -o eth1 -p tcp --dport 53 -d 1.1.1.1 -j ACCEPT" in out
        lines = out.split("\n")
        accept_idx = next(i for i, l in enumerate(lines) if l.endswith("-d 1.1.1.1 -j ACCEPT"))
        reject_idx = next(i for i, l in enumerate(lines) if l == "$IPT -A OUTPUT -o eth1 -p udp --dport 53 -j REJECT")
        assert accept_idx < reject_idx

    def test_custom_ip_literal_emits_no_bootstrap_exception(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["router"]["wan_dns_mode"] = "doh"
        minimal_state["router"]["block_wan_dns"] = True
        minimal_state["router"]["doh_provider"] = "custom"
        minimal_state["router"]["doh_custom_url"] = "https://9.9.9.10/dns-query"
        out = generate(minimal_state)
        assert "-j ACCEPT" not in "\n".join(l for l in out.split("\n") if "--dport 53" in l and "OUTPUT" in l)

    def test_bootstrap_exception_never_weakens_lan_forward_block(self, minimal_state, vlan_10):
        """Even with a custom hostname upstream needing the OUTPUT exception,
        LAN clients' own FORWARD :53 REJECT must be completely unaffected."""
        minimal_state["vlans"] = [vlan_10]
        minimal_state["router"]["wan_dns_mode"] = "doh"
        minimal_state["router"]["block_wan_dns"] = True
        minimal_state["router"]["doh_provider"] = "custom"
        minimal_state["router"]["doh_custom_url"] = "https://doh.example.com/dns-query"
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth0.10 -o eth1 -p udp --dport 53 -j REJECT" in out
        assert "$IPT -A FORWARD -i eth0.10 -o eth1 -p tcp --dport 53 -j REJECT" in out
        assert "$IPT -A FORWARD -i eth0.10 -o eth1 -p udp --dport 53 -d 1.1.1.1 -j ACCEPT" not in out

    def test_no_bootstrap_exception_outside_block_wan_dns(self, minimal_state, vlan_10):
        """Even with a hostname custom upstream, the exception is only ever
        emitted when block_wan_dns is actually active — nothing to work
        around otherwise."""
        minimal_state["vlans"] = [vlan_10]
        minimal_state["router"]["wan_dns_mode"] = "doh"
        minimal_state["router"]["block_wan_dns"] = False
        minimal_state["router"]["doh_provider"] = "custom"
        minimal_state["router"]["doh_custom_url"] = "https://doh.example.com/dns-query"
        out = generate(minimal_state)
        assert "-d 1.1.1.1" not in out


class TestUntaggedPhysicalNetwork:
    """Multi-NIC installs (#195) model an untagged LAN as a VlanConfig with
    vlan_id=0 — the iptables rules must reference the bare physical NIC,
    not a tagged "<if>.0" subinterface, for DNS/DHCP, web UI, and egress."""

    def test_untagged_lan_dns_dhcp_and_webui(self, minimal_state):
        minimal_state["router"]["wan_interface"] = "eth0"
        minimal_state["vlans"] = [{
            "vlan_id": 0, "name": "LAN", "interface": "eth1",
            "ip_address": "192.168.10.1", "prefix_len": 24,
            "dhcp_enabled": True, "dhcp_start": "192.168.10.100",
            "dhcp_end": "192.168.10.200", "dhcp_lease": "12h", "isolate": False,
        }]
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth1 -p udp --dport 53 -j ACCEPT" in out
        assert "$IPT -A INPUT -i eth1 -p tcp --dport 53 -j ACCEPT" in out
        assert "$IPT -A INPUT -i eth1 -p udp --dport 67 -j ACCEPT" in out
        assert "$IPT -A INPUT -i eth1 -p tcp --dport 8080 -j ACCEPT" in out
        assert "eth1.0" not in out

    def test_untagged_lan_egress_to_wan(self, minimal_state):
        minimal_state["router"]["wan_interface"] = "eth0"
        minimal_state["vlans"] = [{
            "vlan_id": 0, "name": "LAN", "interface": "eth1",
            "ip_address": "192.168.10.1", "prefix_len": 24,
            "dhcp_enabled": True, "dhcp_start": "192.168.10.100",
            "dhcp_end": "192.168.10.200", "dhcp_lease": "12h", "isolate": False,
        }]
        out = generate(minimal_state)
        assert "$IPT -A FORWARD -i eth1 -o eth0 -j ACCEPT" in out
        assert "$IPT -t nat -A POSTROUTING -o eth0 -j MASQUERADE" in out
