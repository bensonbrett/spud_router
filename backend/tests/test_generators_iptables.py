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


class TestNat:
    def test_masquerade_on_wan(self, minimal_state):
        out = generate(minimal_state)
        assert "$IPT -t nat -A POSTROUTING -o eth1 -j MASQUERADE" in out

    def test_masquerade_uses_correct_wan_interface(self, minimal_state):
        minimal_state["router"]["wan_interface"] = "enp3s0"
        out = generate(minimal_state)
        assert "POSTROUTING -o enp3s0 -j MASQUERADE" in out


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
