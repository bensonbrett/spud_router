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
        """Secure by default: no icmp_echo toggle set anywhere → no echo-request ACCEPT."""
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        assert "--icmp-type echo-request -j ACCEPT" not in out

    def test_vlan_icmp_echo_enabled_adds_accept(self, minimal_state, vlan_10):
        vlan_10["icmp_echo"] = True
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth0.10 -p icmp --icmp-type echo-request -j ACCEPT" in out

    def test_vlan_icmp_echo_disabled_by_default(self, minimal_state, vlan_10, vlan_20):
        """Only the VLAN with icmp_echo=true gets the accept rule."""
        vlan_10["icmp_echo"] = True
        vlan_20["icmp_echo"] = False
        minimal_state["vlans"] = [vlan_10, vlan_20]
        out = generate(minimal_state)
        assert "-i eth0.10 -p icmp --icmp-type echo-request -j ACCEPT" in out
        assert "-i eth0.20 -p icmp --icmp-type echo-request -j ACCEPT" not in out

    def test_mgmt_icmp_echo_enabled(self, minimal_state):
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        minimal_state["router"]["mgmt_icmp_echo"] = True
        out = generate(minimal_state)
        assert "$IPT -A INPUT -i eth0 -p icmp --icmp-type echo-request -j ACCEPT" in out

    def test_mgmt_icmp_echo_default_blocked(self, minimal_state):
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        out = generate(minimal_state)
        assert "-i eth0 -p icmp --icmp-type echo-request -j ACCEPT" not in out

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
