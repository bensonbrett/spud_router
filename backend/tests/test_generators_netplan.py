# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Tests for generators/netplan.py

The netplan generator is a pure function with no I/O — every case is
fully deterministic and testable without any mocking.
"""
import pytest
from generators.netplan import generate


class TestBasicWan:
    def test_dhcp_wan(self, minimal_state):
        out = generate(minimal_state)
        assert "dhcp4: true" in out
        assert "eth1:" in out

    def test_static_wan(self, minimal_state):
        minimal_state["router"].update({
            "wan_mode":    "static",
            "wan_ip":      "203.0.113.5",
            "wan_prefix":  24,
            "wan_gateway": "203.0.113.1",
            "wan_dns":     "8.8.8.8",
        })
        out = generate(minimal_state)
        assert "addresses: [203.0.113.5/24]" in out
        assert "via: 203.0.113.1" in out
        assert "addresses: [8.8.8.8]" in out
        assert "dhcp4" not in out.split("eth1:")[1].split("\n")[1]

    def test_always_has_network_header(self, minimal_state):
        out = generate(minimal_state)
        assert out.startswith("network:")
        assert "version: 2" in out
        assert "renderer: networkd" in out

    def test_wan_vlan_subinterface_dhcp(self, minimal_state):
        """WAN on a VLAN subinterface goes under vlans:, not ethernets."""
        minimal_state["router"]["wan_interface"] = "eth0.2"
        out = generate(minimal_state)
        assert "eth0:" in out  # parent in ethernets
        assert "eth0: {}" in out  # no IP on parent
        assert "vlans:" in out
        assert "eth0.2:" in out
        assert "id: 2" in out
        assert "link: eth0" in out
        assert "dhcp4: true" in out

    def test_wan_vlan_subinterface_static(self, minimal_state):
        """WAN VLAN with static IP gets addresses, routes, nameservers."""
        minimal_state["router"].update({
            "wan_interface": "eth0.2",
            "wan_mode":      "static",
            "wan_ip":        "203.0.113.5",
            "wan_prefix":    24,
            "wan_gateway":   "203.0.113.1",
            "wan_dns":       "8.8.8.8",
        })
        out = generate(minimal_state)
        vlan_block = out.split("eth0.2:")[1]
        assert "addresses: [203.0.113.5/24]" in vlan_block
        assert "via: 203.0.113.1" in vlan_block
        assert "addresses: [8.8.8.8]" in vlan_block
        assert "dhcp4: false" in vlan_block
        assert "id: 2" in out
        assert "link: eth0" in out

    def test_wan_vlan_with_mgmt_on_same_parent(self, minimal_state, vlan_10):
        """Mgmt IP on eth0 + WAN VLAN eth0.2 + LAN VLAN eth0.10 — all on one port."""
        minimal_state["router"].update({
            "wan_interface":  "eth0.2",
            "mgmt_enabled":   True,
            "mgmt_interface": "eth0",
            "mgmt_ip":        "192.168.1.1",
            "mgmt_prefix":    24,
        })
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        # eth0 should have mgmt IP
        eth0_block = out.split("eth0:")[1].split("eth0.")[0]
        assert "addresses: [192.168.1.1/24]" in eth0_block
        # Both VLANs present
        assert "eth0.2:" in out
        assert "eth0.10:" in out
        assert "id: 2" in out
        assert "id: 10" in out
        assert "link: eth0" in out


class TestVlanSubinterfaces:
    def test_single_vlan_subinterface(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        assert "vlans:" in out
        assert "eth0.10:" in out
        assert "id: 10" in out
        assert "link: eth0" in out
        assert "addresses: [192.168.10.1/24]" in out
        assert "dhcp4: false" in out

    def test_trunk_parent_has_no_ip(self, minimal_state, vlan_10):
        """eth0 carries tagged VLANs — it should appear as an empty ethernets entry."""
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        # eth0 should appear in ethernets but without an address
        ethernets_block = out.split("ethernets:")[1].split("vlans:")[0]
        assert "eth0: {}" in ethernets_block

    def test_multiple_vlans(self, minimal_state, vlan_10, vlan_20):
        minimal_state["vlans"] = [vlan_10, vlan_20]
        out = generate(minimal_state)
        assert "eth0.10:" in out
        assert "eth0.20:" in out

    def test_vlan_without_dhcp_still_gets_ip(self, minimal_state, vlan_10):
        """DHCP is handled by dnsmasq, not netplan — the IP is always set."""
        vlan_10["dhcp_enabled"] = False
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        assert "addresses: [192.168.10.1/24]" in out


class TestStaticRoutes:
    def test_route_on_vlan_subinterface(self, minimal_state, vlan_10):
        minimal_state["vlans"] = [vlan_10]
        minimal_state["static_routes"] = [{
            "destination": "10.0.0.0/8",
            "gateway":     "192.168.10.254",
            "interface":   "eth0.10",
            "description": "Corp VPN",
        }]
        out = generate(minimal_state)
        vlan_block = out.split("eth0.10:")[1]
        assert "routes:" in vlan_block
        assert "to: 10.0.0.0/8" in vlan_block
        assert "via: 192.168.10.254" in vlan_block

    def test_route_on_different_vlan_not_leaked(self, minimal_state, vlan_10, vlan_20):
        """A route on eth0.10 must not appear under eth0.20."""
        minimal_state["vlans"] = [vlan_10, vlan_20]
        minimal_state["static_routes"] = [{
            "destination": "10.0.0.0/8",
            "gateway":     "192.168.10.254",
            "interface":   "eth0.10",
            "description": "",
        }]
        out = generate(minimal_state)
        vlan20_block = out.split("eth0.20:")[1]
        assert "10.0.0.0/8" not in vlan20_block


class TestManagementInterface:
    def test_mgmt_ip_on_trunk_interface(self, minimal_state, vlan_10):
        """When mgmt_interface == trunk parent, assign IP directly on it."""
        minimal_state["router"]["mgmt_enabled"]  = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        minimal_state["router"]["mgmt_ip"]        = "192.168.1.1"
        minimal_state["router"]["mgmt_prefix"]    = 24
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        eth0_block = out.split("eth0:")[1].split("eth0.")[0]
        assert "addresses: [192.168.1.1/24]" in eth0_block
        # Should NOT appear as empty {}
        assert "eth0: {}" not in out

    def test_mgmt_disabled_leaves_trunk_empty(self, minimal_state, vlan_10):
        minimal_state["router"]["mgmt_enabled"] = False
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        assert "eth0: {}" in out

    def test_mgmt_on_dedicated_interface(self, minimal_state):
        """Mgmt on eth2 (not a trunk parent) gets its own ethernets entry."""
        minimal_state["router"]["mgmt_enabled"]  = True
        minimal_state["router"]["mgmt_interface"] = "eth2"
        minimal_state["router"]["mgmt_ip"]        = "10.0.0.1"
        minimal_state["router"]["mgmt_prefix"]    = 24
        out = generate(minimal_state)
        assert "eth2:" in out
        assert "addresses: [10.0.0.1/24]" in out

    def test_mgmt_not_on_wan_interface(self, minimal_state):
        """WAN and mgmt on the same interface is a misconfiguration the UI warns
        about — but the generator should not duplicate the WAN entry."""
        minimal_state["router"]["mgmt_enabled"]  = True
        minimal_state["router"]["mgmt_interface"] = "eth1"  # same as WAN
        out = generate(minimal_state)
        # eth1 should only appear once in ethernets
        assert out.count("eth1:") == 1


class TestUntaggedPhysicalNetwork:
    """Multi-NIC installs (#195) model an untagged LAN as a VlanConfig with
    vlan_id=0 — it must render as a bare ethernets stanza, not a VLAN
    subinterface."""

    def test_untagged_lan_gets_ethernets_entry(self, minimal_state):
        minimal_state["router"]["wan_interface"] = "eth0"
        minimal_state["vlans"] = [{
            "vlan_id": 0, "name": "LAN", "interface": "eth1",
            "ip_address": "192.168.10.1", "prefix_len": 24,
            "dhcp_enabled": True, "dhcp_start": "192.168.10.100",
            "dhcp_end": "192.168.10.200", "dhcp_lease": "12h", "isolate": False,
        }]
        out = generate(minimal_state)
        ethernets_block = out.split("ethernets:")[1].split("vlans:")[0] if "vlans:" in out else out.split("ethernets:")[1]
        assert "eth1:" in ethernets_block
        assert "addresses: [192.168.10.1/24]" in ethernets_block
        # Never a "vlans:" section, and never a bogus eth1.0 subinterface
        assert "vlans:" not in out
        assert "eth1.0" not in out

    def test_untagged_and_tagged_coexist(self, minimal_state, vlan_10):
        """A physical LAN (vlan_id=0) alongside a real tagged VLAN on a
        different interface — both must render correctly and independently."""
        minimal_state["router"]["wan_interface"] = "eth2"
        minimal_state["vlans"] = [
            vlan_10,  # tagged, eth0.10
            {
                "vlan_id": 0, "name": "Guest", "interface": "eth1",
                "ip_address": "192.168.50.1", "prefix_len": 24,
                "dhcp_enabled": True, "dhcp_start": "192.168.50.100",
                "dhcp_end": "192.168.50.200", "dhcp_lease": "12h", "isolate": False,
            },
        ]
        out = generate(minimal_state)
        assert "eth0.10:" in out
        assert "id: 10" in out
        ethernets_block = out.split("ethernets:")[1].split("vlans:")[0]
        assert "eth1:" in ethernets_block
        assert "addresses: [192.168.50.1/24]" in ethernets_block
        assert "eth1.0" not in out

    def test_multi_nic_wan_lan_topology(self, minimal_state):
        """Full multi-NIC shape from issue #195 §4: WAN on eth0 (physical,
        DHCP), LAN on eth1 (physical, untagged)."""
        minimal_state["router"]["wan_interface"] = "eth0"
        minimal_state["router"]["wan_mode"] = "dhcp"
        minimal_state["vlans"] = [{
            "vlan_id": 0, "name": "LAN", "interface": "eth1",
            "ip_address": "192.168.10.1", "prefix_len": 24,
            "dhcp_enabled": True, "dhcp_start": "192.168.10.100",
            "dhcp_end": "192.168.10.200", "dhcp_lease": "12h", "isolate": False,
        }]
        out = generate(minimal_state)
        assert "eth0:" in out
        assert "dhcp4: true" in out
        assert "addresses: [192.168.10.1/24]" in out
        assert "vlans:" not in out

    def test_untagged_lan_not_duplicated_as_mgmt(self, minimal_state):
        """When mgmt is folded into the untagged LAN interface (mgmt_enabled
        stays False in that case) the interface must only be emitted once."""
        minimal_state["router"]["wan_interface"] = "eth0"
        minimal_state["router"]["mgmt_enabled"] = False
        minimal_state["router"]["mgmt_interface"] = "eth1"
        minimal_state["vlans"] = [{
            "vlan_id": 0, "name": "LAN", "interface": "eth1",
            "ip_address": "192.168.10.1", "prefix_len": 24,
            "dhcp_enabled": True, "dhcp_start": "192.168.10.100",
            "dhcp_end": "192.168.10.200", "dhcp_lease": "12h", "isolate": False,
        }]
        out = generate(minimal_state)
        assert out.count("eth1:") == 1


class TestLanPlusTaggedMgmtVlanOnSameNic:
    """Issue #207's 2-NIC "management VLAN" composition: one physical NIC
    carries BOTH an untagged LAN network (vlan_id=0) and a tagged management
    VLAN subinterface at once. This is a real gap the plain untagged-only and
    tagged-only cases above never exercised: the NIC must get its own address
    (the untagged LAN) even though it's also a trunk carrier for the tagged
    mgmt subinterface — regression coverage for a bug caught while building
    #207 (the parent was being emitted as a bare `{}` carrier, silently
    dropping the LAN's own address, with a duplicate/invalid mgmt stanza also
    appearing under ethernets:)."""

    def _state(self, minimal_state):
        minimal_state["router"]["wan_interface"] = "eth0"
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth1.99"
        minimal_state["router"]["mgmt_ip"] = "192.168.1.1"
        minimal_state["router"]["mgmt_prefix"] = 24
        minimal_state["vlans"] = [
            {
                "vlan_id": 0, "name": "LAN", "interface": "eth1",
                "ip_address": "192.168.10.1", "prefix_len": 24,
                "dhcp_enabled": True, "dhcp_start": "192.168.10.100",
                "dhcp_end": "192.168.10.200", "dhcp_lease": "12h", "isolate": False,
            },
            {
                "vlan_id": 99, "name": "Management", "interface": "eth1",
                "ip_address": "192.168.1.1", "prefix_len": 24,
                "dhcp_enabled": True, "dhcp_start": "192.168.1.100",
                "dhcp_end": "192.168.1.150", "dhcp_lease": "12h", "isolate": False,
            },
        ]
        return minimal_state

    def test_nic_keeps_its_own_untagged_address(self, minimal_state):
        out = generate(self._state(minimal_state))
        ethernets_block = out.split("ethernets:")[1].split("vlans:")[0]
        assert "eth1:" in ethernets_block
        assert "addresses: [192.168.10.1/24]" in ethernets_block
        assert "eth1: {}" not in out

    def test_mgmt_vlan_rendered_as_tagged_subinterface(self, minimal_state):
        out = generate(self._state(minimal_state))
        vlans_block = out.split("vlans:")[1]
        assert "eth1.99:" in vlans_block
        assert "id: 99" in vlans_block
        assert "link: eth1" in vlans_block
        assert "addresses: [192.168.1.1/24]" in vlans_block

    def test_no_bogus_dotted_ethernets_entry(self, minimal_state):
        """mgmt_interface ("eth1.99") must never be treated as a bare
        physical interface under ethernets: — it's a VLAN, not a NIC."""
        out = generate(self._state(minimal_state))
        ethernets_block = out.split("ethernets:")[1].split("vlans:")[0]
        assert "eth1.99" not in ethernets_block
        assert out.count("eth1.99:") == 1


class TestMgmtDhcpAddressing:
    """Issue #213 — management interface DHCP-client addressing. static
    (default, missing-field-safe) must stay byte-for-byte identical to
    pre-#213 output; dhcp mode must emit the anti-lockout dhcp4-overrides."""

    def test_dedicated_port_static_unchanged(self, minimal_state):
        """Missing mgmt_addr_mode (pre-#213 state.json) behaves exactly
        like explicit 'static' — the backward-compat guarantee."""
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth2"
        minimal_state["router"]["mgmt_ip"] = "10.0.0.1"
        minimal_state["router"]["mgmt_prefix"] = 24
        out = generate(minimal_state)
        assert "addresses: [10.0.0.1/24]" in out
        assert "dhcp4-overrides" not in out

    def test_dedicated_port_dhcp_mode(self, minimal_state):
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth2"
        minimal_state["router"]["mgmt_addr_mode"] = "dhcp"
        out = generate(minimal_state)
        eth2_block = out.split("eth2:")[1].split("\n\n")[0]
        assert "dhcp4: true" in eth2_block
        assert "dhcp4-overrides:" in eth2_block
        assert "use-routes: false" in eth2_block
        assert "use-dns: false" in eth2_block
        assert "addresses:" not in eth2_block

    def test_trunk_parent_mgmt_dhcp_mode(self, minimal_state, vlan_10):
        """Untagged mgmt sharing a trunk NIC (1-NIC-style) can also use
        dhcp mode, per the plan — even though the installer default stays
        static/serving for that tier."""
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        minimal_state["router"]["mgmt_addr_mode"] = "dhcp"
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        eth0_block = out.split("eth0:")[1].split("\n\n")[0]
        assert "dhcp4: true" in eth0_block
        assert "use-routes: false" in eth0_block
        assert "use-dns: false" in eth0_block

    def test_trunk_parent_mgmt_static_unchanged(self, minimal_state, vlan_10):
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth0"
        minimal_state["router"]["mgmt_ip"] = "192.168.1.1"
        minimal_state["router"]["mgmt_prefix"] = 24
        minimal_state["vlans"] = [vlan_10]
        out = generate(minimal_state)
        eth0_block = out.split("eth0:")[1].split("eth0.")[0]
        assert "addresses: [192.168.1.1/24]" in eth0_block
        assert "dhcp4-overrides" not in out

    def test_tagged_mgmt_vlan_dhcp_mode(self, minimal_state):
        """2-NIC "vlan" mode's mgmt VLAN can also take a DHCP lease."""
        minimal_state["router"]["wan_interface"] = "eth0"
        minimal_state["router"]["mgmt_enabled"] = True
        minimal_state["router"]["mgmt_interface"] = "eth1.99"
        minimal_state["router"]["mgmt_addr_mode"] = "dhcp"
        minimal_state["vlans"] = [
            {
                "vlan_id": 0, "name": "LAN", "interface": "eth1",
                "ip_address": "192.168.10.1", "prefix_len": 24,
                "dhcp_enabled": True, "dhcp_start": "192.168.10.100",
                "dhcp_end": "192.168.10.200", "dhcp_lease": "12h", "isolate": False,
            },
            {
                "vlan_id": 99, "name": "Management", "interface": "eth1",
                "ip_address": "192.168.1.1", "prefix_len": 24,
                "dhcp_enabled": False, "dhcp_start": "192.168.1.100",
                "dhcp_end": "192.168.1.150", "dhcp_lease": "12h", "isolate": False,
            },
        ]
        out = generate(minimal_state)
        vlans_block = out.split("vlans:")[1]
        eth1_99_block = vlans_block.split("eth1.99:")[1]
        assert "dhcp4: true" in eth1_99_block
        assert "use-routes: false" in eth1_99_block
        assert "use-dns: false" in eth1_99_block
        assert "addresses:" not in eth1_99_block
        # LAN itself (untagged, on the same NIC) must be untouched
        ethernets_block = out.split("ethernets:")[1].split("vlans:")[0]
        assert "addresses: [192.168.10.1/24]" in ethernets_block
