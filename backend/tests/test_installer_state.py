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


# Golden states for the tiered topologies (issue #207). Each is the exact
# state.json install.sh writes for that tier's default (non-interactive, or
# TTY-accepted-default) path — a byte-for-byte regression anchor, same
# purpose as GOLDEN_SINGLE_NIC_DEFAULT above.
GOLDEN_2NIC_FLAT = (
    '{"vlans":[{"vlan_id":0,"name":"LAN","interface":"eth1","ip_address":"192.168.10.1","prefix_len":24,'
    '"dhcp_enabled":true,"dhcp_start":"192.168.10.100","dhcp_end":"192.168.10.200","dhcp_lease":"12h",'
    '"isolate":false}],"router":{"wan_interface":"eth0","wan_mode":"dhcp","wan_dns_mode":"auto",'
    '"wan_dns":"1.1.1.1","wan_dns_alt":"8.8.8.8","hostname":"spud-router","mgmt_enabled":false,'
    '"mgmt_interface":"eth1","mgmt_ip":"192.168.1.1","mgmt_prefix":24,"mgmt_dhcp_start":"192.168.1.100",'
    '"mgmt_dhcp_end":"192.168.1.150","mgmt_dhcp_lease":"12h"},"static_routes":[],"dns_entries":[],'
    '"tailscale":{"enabled":false,"advertise_routes":[],"exit_node":false,"accept_routes":true},'
    '"fw_inbound":[],"fw_intervlan":[]}'
)

GOLDEN_2NIC_MGMT_VLAN = (
    '{"vlans":[{"vlan_id":0,"name":"LAN","interface":"eth1","ip_address":"192.168.10.1","prefix_len":24,'
    '"dhcp_enabled":true,"dhcp_start":"192.168.10.100","dhcp_end":"192.168.10.200","dhcp_lease":"12h",'
    '"isolate":false},{"vlan_id":99,"name":"Management","interface":"eth1","ip_address":"192.168.1.1",'
    '"prefix_len":24,"dhcp_enabled":false,"dhcp_start":"192.168.1.100","dhcp_end":"192.168.1.150",'
    '"dhcp_lease":"12h","isolate":false}],"router":{"wan_interface":"eth0","wan_mode":"dhcp",'
    '"wan_dns_mode":"auto","wan_dns":"1.1.1.1","wan_dns_alt":"8.8.8.8","hostname":"spud-router",'
    '"mgmt_enabled":true,"mgmt_interface":"eth1.99","mgmt_ip":"192.168.1.1","mgmt_prefix":24,'
    '"mgmt_dhcp_start":"192.168.1.100","mgmt_dhcp_end":"192.168.1.150","mgmt_dhcp_lease":"12h",'
    '"mgmt_addr_mode":"dhcp","mgmt_dhcp_server":false},'
    '"static_routes":[],"dns_entries":[],'
    '"tailscale":{"enabled":false,"advertise_routes":[],"exit_node":false,"accept_routes":true},'
    '"fw_inbound":[],"fw_intervlan":[]}'
)

GOLDEN_3NIC = (
    '{"vlans":[{"vlan_id":0,"name":"LAN","interface":"eth1","ip_address":"192.168.10.1","prefix_len":24,'
    '"dhcp_enabled":true,"dhcp_start":"192.168.10.100","dhcp_end":"192.168.10.200","dhcp_lease":"12h",'
    '"isolate":false}],"router":{"wan_interface":"eth0","wan_mode":"dhcp","wan_dns_mode":"auto",'
    '"wan_dns":"1.1.1.1","wan_dns_alt":"8.8.8.8","hostname":"spud-router","mgmt_enabled":true,'
    '"mgmt_interface":"eth2","mgmt_ip":"192.168.1.1","mgmt_prefix":24,"mgmt_dhcp_start":"192.168.1.100",'
    '"mgmt_dhcp_end":"192.168.1.150","mgmt_dhcp_lease":"12h","mgmt_addr_mode":"dhcp","mgmt_dhcp_server":false},'
    '"static_routes":[],"dns_entries":[],'
    '"tailscale":{"enabled":false,"advertise_routes":[],"exit_node":false,"accept_routes":true},'
    '"fw_inbound":[],"fw_intervlan":[]}'
)


class TestGoldenTieredTopologies:
    """Byte-for-byte regression anchors for each NIC-count tier (#207)."""

    def test_2nic_flat_default(self):
        """2 NICs, mgmt-VLAN prompt declined (the default) — today's
        multi_nic_state behavior, unchanged by #207."""
        assert render(multi_nic_state(wan_if="eth0", lan_if="eth1")) == GOLDEN_2NIC_FLAT

    def test_2nic_mgmt_vlan(self):
        """2 NICs, mgmt-VLAN prompt accepted — untagged LAN + tagged mgmt
        VLAN 99 composed onto the same LAN NIC. Default addressing is DHCP
        with no local server (#213) — joining an existing management
        network is the expected multi-NIC scenario."""
        state = multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_vlan_id=99)
        assert render(state) == GOLDEN_2NIC_MGMT_VLAN

    def test_3nic_dedicated_mgmt(self):
        """3 NICs — WAN, LAN, and a dedicated physical mgmt port, each on
        its own interface. Default addressing is DHCP with no local server
        (#213)."""
        state = multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_if="eth2")
        assert render(state) == GOLDEN_3NIC


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


class TestMultiNicMgmtVlan:
    """2-NIC tier, "yes" branch (issue #207): LAN stays untagged, and a
    second tagged VLAN carries management on the same physical LAN NIC."""

    def test_untagged_lan_plus_tagged_mgmt_vlan(self):
        state = multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_vlan_id=99)
        assert len(state["vlans"]) == 2
        lan = next(v for v in state["vlans"] if v["name"] == "LAN")
        mgmt = next(v for v in state["vlans"] if v["name"] == "Management")
        assert lan["vlan_id"] == 0
        assert lan["interface"] == "eth1"
        assert mgmt["vlan_id"] == 99
        assert mgmt["interface"] == "eth1"
        assert mgmt["ip_address"] == "192.168.1.1"

    def test_mgmt_enabled_and_interface_is_subinterface(self):
        state = multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_vlan_id=99)
        assert state["router"]["mgmt_enabled"] is True
        assert state["router"]["mgmt_interface"] == "eth1.99"

    def test_wan_untouched_by_mgmt_vlan(self):
        state = multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_vlan_id=99)
        assert state["router"]["wan_interface"] == "eth0"

    def test_custom_mgmt_subnet(self):
        state = multi_nic_state(
            wan_if="eth0", lan_if="eth1", mgmt_vlan_id=50,
            mgmt_cidr="10.0.0.1/24", mgmt_dhcp_start="10.0.0.50", mgmt_dhcp_end="10.0.0.60",
        )
        mgmt = next(v for v in state["vlans"] if v["name"] == "Management")
        assert mgmt["ip_address"] == "10.0.0.1"
        assert mgmt["dhcp_start"] == "10.0.0.50"
        assert mgmt["dhcp_end"] == "10.0.0.60"
        assert state["router"]["mgmt_ip"] == "10.0.0.1"

    def test_mgmt_if_and_mgmt_vlan_id_mutually_exclusive(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_if="eth2", mgmt_vlan_id=99)

    def test_invalid_mgmt_vlan_id_rejected(self):
        with pytest.raises(ValueError):
            multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_vlan_id=9999)


class TestMgmtAddrModeDefaults:
    """Issue #213 — management addressing mode. Default is "dhcp" (no
    local server) whenever mgmt is enabled, for BOTH the "nic" (dedicated
    port) and "vlan" (tagged mgmt VLAN) modes: joining an existing
    management network is the expected multi-NIC scenario, not spud-router
    owning yet another subnet."""

    def test_nic_mode_defaults_to_dhcp_no_server(self):
        state = multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_if="eth2")
        assert state["router"]["mgmt_addr_mode"] == "dhcp"
        assert state["router"]["mgmt_dhcp_server"] is False

    def test_vlan_mode_defaults_to_dhcp_no_server(self):
        state = multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_vlan_id=99)
        assert state["router"]["mgmt_addr_mode"] == "dhcp"
        assert state["router"]["mgmt_dhcp_server"] is False

    def test_vlan_mode_dhcp_default_disables_its_own_dhcp_scope(self):
        """The Management VlanConfig entry is the ONLY thing that can serve
        DHCP for a tagged mgmt VLAN (the dedicated mgmt block in dnsmasq.py
        never fires for a dotted mgmt_if) — so the default (no server) must
        flow through to the VLAN entry's own dhcp_enabled, not just the
        router-level flag."""
        state = multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_vlan_id=99)
        mgmt_vlan = next(v for v in state["vlans"] if v["name"] == "Management")
        assert mgmt_vlan["dhcp_enabled"] is False

    def test_flat_lan_mode_has_no_addr_mode_fields(self):
        """Folded mgmt (mgmt_enabled=False) — the fields are irrelevant and
        omitted entirely, keeping that golden state untouched."""
        state = multi_nic_state(wan_if="eth0", lan_if="eth1")
        assert "mgmt_addr_mode" not in state["router"]
        assert "mgmt_dhcp_server" not in state["router"]

    def test_explicit_static_override_preserves_pre_213_behavior(self):
        """An operator who wants spud-router to keep owning its own mgmt
        DHCP on a dedicated port can still opt back into that."""
        state = multi_nic_state(
            wan_if="eth0", lan_if="eth1", mgmt_if="eth2",
            mgmt_addr_mode="static", mgmt_dhcp_server=True,
        )
        assert state["router"]["mgmt_addr_mode"] == "static"
        assert state["router"]["mgmt_dhcp_server"] is True

    def test_static_override_alone_defaults_server_to_true(self):
        """Overriding ONLY mgmt_addr_mode="static" (not mgmt_dhcp_server)
        must still default to serving — "static" means spud-router owns
        this segment, same as it always has. Only "dhcp" mode defaults to
        not serving. (Regression check: the resolved default must depend
        on the addr mode, not be a flat False regardless of it.)"""
        state = multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_if="eth2", mgmt_addr_mode="static")
        assert state["router"]["mgmt_dhcp_server"] is True

    def test_vlan_mode_static_override_keeps_serving_its_own_scope(self):
        state = multi_nic_state(
            wan_if="eth0", lan_if="eth1", mgmt_vlan_id=99,
            mgmt_addr_mode="static", mgmt_dhcp_server=True,
        )
        mgmt_vlan = next(v for v in state["vlans"] if v["name"] == "Management")
        assert mgmt_vlan["dhcp_enabled"] is True

    def test_dhcp_with_server_true_rejected(self):
        with pytest.raises(ValueError, match="mgmt_dhcp_server must be false"):
            multi_nic_state(
                wan_if="eth0", lan_if="eth1", mgmt_if="eth2",
                mgmt_addr_mode="dhcp", mgmt_dhcp_server=True,
            )

    def test_invalid_addr_mode_rejected(self):
        with pytest.raises(ValueError, match="mgmt_addr_mode must be"):
            multi_nic_state(wan_if="eth0", lan_if="eth1", mgmt_if="eth2", mgmt_addr_mode="pppoe")


class TestRender:
    def test_render_is_compact_json(self):
        out = render({"a": 1, "b": [1, 2]})
        assert out == '{"a":1,"b":[1,2]}'
