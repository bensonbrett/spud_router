# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Tests for installer_state.py (issue #195 — multi-NIC installer).

The golden-state test is the regression anchor for the single-NIC install
path: install.sh must write byte-for-byte this same JSON whenever defaults
are accepted (or the install is non-interactive) — zero behavior change
from before #195.
"""
import pytest

from installer_state import (
    custom_single_nic_state,
    default_single_nic_state,
    multi_nic_state,
    render,
    suggest_dhcp_range,
    validate_cidr,
    validate_dhcp_range,
    validate_ip,
    validate_vlan_id,
)

# The exact literal install.sh has always written (install.sh:271, pre-#195),
# with MGMT_IF="eth0". Do not change this without also verifying the
# corresponding install.sh template is intentionally being changed too.
GOLDEN_SINGLE_NIC_DEFAULT = (
    '{"vlans":[{"vlan_id":2,"name":"WAN","interface":"eth0","ip_address":"","prefix_len":0,'
    '"dhcp_enabled":false,"dhcp_start":"","dhcp_end":"","dhcp_lease":"12h","isolate":false},'
    '{"vlan_id":10,"name":"LAN","interface":"eth0","ip_address":"192.168.10.1","prefix_len":24,'
    '"dhcp_enabled":true,"dhcp_start":"192.168.10.100","dhcp_end":"192.168.10.200","dhcp_lease":"12h",'
    '"isolate":false}],"router":{"wan_interface":"eth0.2","wan_mode":"dhcp","wan_dns_mode":"auto",'
    '"wan_dns":"1.1.1.1","wan_dns_alt":"8.8.8.8","hostname":"spud-router","mgmt_enabled":true,'
    '"mgmt_interface":"eth0","mgmt_ip":"192.168.1.1","mgmt_prefix":24,"mgmt_dhcp_start":"192.168.1.100",'
    '"mgmt_dhcp_end":"192.168.1.150","mgmt_dhcp_lease":"12h"},"static_routes":[],"dns_entries":[],'
    '"tailscale":{"enabled":false,"advertise_routes":[],"exit_node":false,"accept_routes":true},'
    '"fw_inbound":[],"fw_intervlan":[]}'
)


class TestGoldenSingleNicDefault:
    def test_byte_for_byte_matches_install_sh_literal(self):
        """The regression anchor: accepting defaults (or a non-interactive
        install) must never change the single-NIC state.json."""
        assert render(default_single_nic_state("eth0")) == GOLDEN_SINGLE_NIC_DEFAULT

    def test_trunk_if_is_substituted_everywhere(self):
        state = default_single_nic_state("enp1s0")
        assert state["router"]["wan_interface"] == "enp1s0.2"
        assert state["router"]["mgmt_interface"] == "enp1s0"
        assert state["vlans"][0]["interface"] == "enp1s0"
        assert state["vlans"][1]["interface"] == "enp1s0"


class TestValidators:
    def test_vlan_id_valid(self):
        assert validate_vlan_id("10") == 10

    @pytest.mark.parametrize("v", ["0", "4095", "-1", "abc", ""])
    def test_vlan_id_invalid(self, v):
        with pytest.raises(ValueError):
            validate_vlan_id(v)

    def test_ip_valid(self):
        assert validate_ip("192.168.10.1") == "192.168.10.1"

    def test_ip_invalid(self):
        with pytest.raises(ValueError):
            validate_ip("999.1.1.1")

    def test_cidr_valid(self):
        assert validate_cidr("192.168.10.1/24") == ("192.168.10.1", 24)

    @pytest.mark.parametrize("v", ["192.168.10.1", "192.168.10.1/33", "not-an-ip/24", "192.168.10.1/0"])
    def test_cidr_invalid(self, v):
        with pytest.raises(ValueError):
            validate_cidr(v)

    def test_dhcp_range_valid(self):
        assert validate_dhcp_range("192.168.10.1/24", "192.168.10.100", "192.168.10.200") == (
            "192.168.10.100", "192.168.10.200",
        )

    def test_dhcp_range_outside_subnet_rejected(self):
        with pytest.raises(ValueError, match="not within"):
            validate_dhcp_range("192.168.10.1/24", "10.0.0.100", "10.0.0.200")

    def test_dhcp_range_end_before_start_rejected(self):
        with pytest.raises(ValueError, match="must not be after"):
            validate_dhcp_range("192.168.10.1/24", "192.168.10.200", "192.168.10.100")


class TestSuggestDhcpRange:
    def test_slash_24_uses_conventional_100_200(self):
        assert suggest_dhcp_range("192.168.10.1/24", 200) == ("192.168.10.100", "192.168.10.200")

    def test_slash_24_mgmt_offset_150(self):
        assert suggest_dhcp_range("192.168.1.1/24", 150) == ("192.168.1.100", "192.168.1.150")

    def test_small_subnet_falls_back_within_range(self):
        start, end = suggest_dhcp_range("192.168.10.1/29", 200)
        network_start, network_prefix = validate_cidr("192.168.10.1/29")
        # Both suggested addresses must actually be usable in this tiny subnet.
        validate_dhcp_range("192.168.10.1/29", start, end)

    def test_too_small_for_any_range_raises(self):
        with pytest.raises(ValueError):
            suggest_dhcp_range("192.168.10.1/31", 200)


class TestCustomSingleNicState:
    def test_custom_vlan_ids_and_ips_flow_through(self):
        state = custom_single_nic_state(
            trunk_if="eth0",
            lan_vlan_id=20,
            lan_cidr="10.0.0.1/24",
            lan_dhcp_start="10.0.0.50",
            lan_dhcp_end="10.0.0.99",
            wan_vlan_id=3,
        )
        assert state["router"]["wan_interface"] == "eth0.3"
        assert state["router"]["wan_mode"] == "dhcp"
        lan_vlan = next(v for v in state["vlans"] if v["name"] == "LAN")
        assert lan_vlan["vlan_id"] == 20
        assert lan_vlan["ip_address"] == "10.0.0.1"
        assert lan_vlan["prefix_len"] == 24
        assert lan_vlan["dhcp_start"] == "10.0.0.50"
        assert lan_vlan["dhcp_end"] == "10.0.0.99"

    def test_static_wan_requires_cidr_and_gateway(self):
        with pytest.raises(ValueError, match="required when wan_mode"):
            custom_single_nic_state(
                trunk_if="eth0", lan_vlan_id=10, lan_cidr="192.168.10.1/24",
                lan_dhcp_start="192.168.10.100", lan_dhcp_end="192.168.10.200",
                wan_vlan_id=2, wan_mode="static",
            )

    def test_static_wan_populates_router_fields(self):
        state = custom_single_nic_state(
            trunk_if="eth0", lan_vlan_id=10, lan_cidr="192.168.10.1/24",
            lan_dhcp_start="192.168.10.100", lan_dhcp_end="192.168.10.200",
            wan_vlan_id=2, wan_mode="static",
            wan_cidr="203.0.113.5/24", wan_gateway="203.0.113.1",
        )
        assert state["router"]["wan_ip"] == "203.0.113.5"
        assert state["router"]["wan_prefix"] == 24
        assert state["router"]["wan_gateway"] == "203.0.113.1"

    def test_lan_and_wan_vlan_id_collision_rejected(self):
        with pytest.raises(ValueError, match="must differ"):
            custom_single_nic_state(
                trunk_if="eth0", lan_vlan_id=2, lan_cidr="192.168.10.1/24",
                lan_dhcp_start="192.168.10.100", lan_dhcp_end="192.168.10.200",
                wan_vlan_id=2,
            )

    def test_dhcp_range_outside_custom_subnet_rejected(self):
        with pytest.raises(ValueError, match="not within"):
            custom_single_nic_state(
                trunk_if="eth0", lan_vlan_id=10, lan_cidr="192.168.10.1/24",
                lan_dhcp_start="10.0.0.100", lan_dhcp_end="10.0.0.200",
                wan_vlan_id=2,
            )


class TestMultiNicState:
    def test_wan_and_lan_on_separate_physical_interfaces(self):
        state = multi_nic_state(wan_if="eth0", lan_if="eth1")
        assert state["router"]["wan_interface"] == "eth0"
        assert state["router"]["wan_mode"] == "dhcp"
        assert len(state["vlans"]) == 1
        lan = state["vlans"][0]
        assert lan["vlan_id"] == 0
        assert lan["interface"] == "eth1"
        assert lan["ip_address"] == "192.168.10.1"
        assert lan["prefix_len"] == 24

    def test_no_separate_mgmt_folds_into_lan(self):
        """No mgmt_if given — mgmt is folded into LAN (mgmt_enabled False),
        matching plan §4's guidance for the common two-NIC case."""
        state = multi_nic_state(wan_if="eth0", lan_if="eth1")
        assert state["router"]["mgmt_enabled"] is False
        assert state["router"]["mgmt_interface"] == "eth1"

    def test_mgmt_same_as_lan_also_folds(self):
        state = multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_if="eth1")
        assert state["router"]["mgmt_enabled"] is False

    def test_separate_mgmt_interface_used(self):
        state = multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_if="eth2")
        assert state["router"]["mgmt_enabled"] is True
        assert state["router"]["mgmt_interface"] == "eth2"
        assert state["router"]["mgmt_ip"] == "192.168.1.1"

    def test_wan_equals_lan_rejected(self):
        with pytest.raises(ValueError, match="must be different"):
            multi_nic_state(wan_if="eth0", lan_if="eth0")

    def test_mgmt_equals_wan_rejected(self):
        with pytest.raises(ValueError, match="must differ from WAN"):
            multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_if="eth0")

    def test_custom_lan_subnet(self):
        state = multi_nic_state(
            wan_if="eth0", lan_if="eth1",
            lan_cidr="10.10.0.1/24", lan_dhcp_start="10.10.0.50", lan_dhcp_end="10.10.0.60",
        )
        lan = state["vlans"][0]
        assert lan["ip_address"] == "10.10.0.1"
        assert lan["dhcp_start"] == "10.10.0.50"
        assert lan["dhcp_end"] == "10.10.0.60"


class TestRender:
    def test_render_is_compact_json(self):
        out = render({"a": 1, "b": [1, 2]})
        assert out == '{"a":1,"b":[1,2]}'
