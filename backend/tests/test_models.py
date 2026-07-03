"""
Tests for models.py — Pydantic validation.

These tests verify that invalid input is rejected with meaningful errors
before it reaches the config generators or gets persisted to state.json.
"""
import pytest
from pydantic import ValidationError

from models import (
    DnsEntry,
    InboundRule,
    InterVlanRule,
    OutboundRule,
    RouterConfig,
    SnmpConfig,
    SNMP_MASKED_SENTINEL,
    StaticRoute,
    SyslogConfig,
    TailscaleConfig,
    VlanConfig,
)


class TestVlanConfig:
    def test_valid_vlan(self):
        v = VlanConfig(
            vlan_id=10, name="Trusted", interface="eth0",
            ip_address="192.168.10.1", prefix_len=24,
        )
        assert v.vlan_id == 10

    def test_vlan_id_zero_rejected(self):
        with pytest.raises(ValidationError, match="between 1 and 4094"):
            VlanConfig(vlan_id=0, name="X", interface="eth0",
                       ip_address="192.168.1.1", prefix_len=24)

    def test_vlan_id_4095_rejected(self):
        with pytest.raises(ValidationError):
            VlanConfig(vlan_id=4095, name="X", interface="eth0",
                       ip_address="192.168.1.1", prefix_len=24)

    def test_invalid_ip_rejected(self):
        with pytest.raises(ValidationError, match="Invalid IP"):
            VlanConfig(vlan_id=10, name="X", interface="eth0",
                       ip_address="999.999.999.999", prefix_len=24)

    def test_invalid_prefix_rejected(self):
        with pytest.raises(ValidationError):
            VlanConfig(vlan_id=10, name="X", interface="eth0",
                       ip_address="192.168.1.1", prefix_len=31)

    def test_interface_name_too_long(self):
        with pytest.raises(ValidationError):
            VlanConfig(vlan_id=10, name="X", interface="a" * 16,
                       ip_address="192.168.1.1", prefix_len=24)

    def test_interface_special_chars_rejected(self):
        with pytest.raises(ValidationError):
            VlanConfig(vlan_id=10, name="X", interface="eth0; rm -rf /",
                       ip_address="192.168.1.1", prefix_len=24)

    def test_dns_server_defaults_empty(self):
        v = VlanConfig(vlan_id=10, name="X", interface="eth0",
                        ip_address="192.168.1.1", prefix_len=24)
        assert v.dns_server == ""

    def test_dns_server_invalid_rejected(self):
        with pytest.raises(ValidationError, match="Invalid IP"):
            VlanConfig(vlan_id=10, name="X", interface="eth0",
                       ip_address="192.168.1.1", prefix_len=24, dns_server="not-an-ip")

    def test_dhcp_options_defaults_empty(self):
        v = VlanConfig(vlan_id=10, name="X", interface="eth0",
                        ip_address="192.168.1.1", prefix_len=24)
        assert v.dhcp_options == []

    def test_dhcp_options_newline_rejected(self):
        with pytest.raises(ValidationError, match="newlines"):
            VlanConfig(vlan_id=10, name="X", interface="eth0",
                       ip_address="192.168.1.1", prefix_len=24,
                       dhcp_options=["42,192.168.1.1\nserver=evil.com"])

    def test_icmp_echo_defaults_blocked(self):
        v = VlanConfig(vlan_id=10, name="X", interface="eth0",
                        ip_address="192.168.1.1", prefix_len=24)
        assert v.icmp_echo is False

    def test_dhcp_options_too_long_rejected(self):
        with pytest.raises(ValidationError):
            VlanConfig(vlan_id=10, name="X", interface="eth0",
                       ip_address="192.168.1.1", prefix_len=24,
                       dhcp_options=["x" * 201])


class TestRouterConfig:
    def test_valid_dhcp(self):
        r = RouterConfig(wan_interface="eth1", wan_mode="dhcp")
        assert r.wan_mode == "dhcp"

    def test_invalid_wan_mode(self):
        with pytest.raises(ValidationError, match="dhcp.*static"):
            RouterConfig(wan_interface="eth1", wan_mode="pppoe")

    def test_invalid_hostname(self):
        with pytest.raises(ValidationError, match="hostname"):
            RouterConfig(wan_interface="eth1", wan_mode="dhcp",
                         hostname="my router!")  # spaces and ! not allowed

    def test_hostname_with_hyphens_ok(self):
        r = RouterConfig(wan_interface="eth1", wan_mode="dhcp",
                         hostname="my-router-01")
        assert r.hostname == "my-router-01"

    def test_wan_interface_allows_vlan_subinterface(self):
        """WAN on a VLAN subinterface (router-on-a-stick) must accept dots."""
        r = RouterConfig(wan_interface="eth0.2", wan_mode="dhcp")
        assert r.wan_interface == "eth0.2"

    def test_mgmt_icmp_echo_defaults_blocked(self):
        r = RouterConfig(wan_interface="eth1", wan_mode="dhcp")
        assert r.mgmt_icmp_echo is False


class TestStaticRoute:
    def test_valid_route(self):
        r = StaticRoute(destination="10.0.0.0/8", gateway="192.168.10.254")
        assert r.destination == "10.0.0.0/8"

    def test_invalid_cidr(self):
        with pytest.raises(ValidationError, match="Invalid CIDR"):
            StaticRoute(destination="not-a-cidr", gateway="192.168.1.1")

    def test_host_cidr_normalised(self):
        """192.168.1.1/24 is valid (host bits set) — we accept with strict=False."""
        r = StaticRoute(destination="192.168.1.5/24", gateway="192.168.1.1")
        assert r.destination == "192.168.1.5/24"

    def test_invalid_gateway(self):
        with pytest.raises(ValidationError, match="Invalid gateway"):
            StaticRoute(destination="10.0.0.0/8", gateway="not-an-ip")


class TestDnsEntry:
    def test_valid_entry(self):
        e = DnsEntry(hostname="nas", ip="192.168.10.10")
        assert e.hostname == "nas"

    def test_invalid_hostname_spaces(self):
        with pytest.raises(ValidationError):
            DnsEntry(hostname="my nas", ip="192.168.10.10")

    def test_invalid_ip(self):
        with pytest.raises(ValidationError, match="Invalid IP"):
            DnsEntry(hostname="nas", ip="256.0.0.1")

    def test_fqdn_accepted(self):
        e = DnsEntry(hostname="nas.local.corp", ip="10.0.0.1")
        assert e.hostname == "nas.local.corp"


class TestInboundRule:
    def test_valid_rule(self):
        r = InboundRule(vlan_id=10, proto="tcp", port=22, action="accept")
        assert r.port == 22

    def test_invalid_proto(self):
        with pytest.raises(ValidationError, match="tcp, udp, any, or icmp"):
            InboundRule(proto="sctp")

    def test_icmp_proto_accepted(self):
        r = InboundRule(proto="icmp")
        assert r.proto == "icmp"

    def test_invalid_action(self):
        with pytest.raises(ValidationError, match="accept or drop"):
            InboundRule(action="reject")

    def test_port_out_of_range(self):
        with pytest.raises(ValidationError, match="between 1 and 65535"):
            InboundRule(port=70000)

    def test_port_zero_rejected(self):
        with pytest.raises(ValidationError):
            InboundRule(port=0)

    def test_port_none_is_valid(self):
        r = InboundRule(port=None)
        assert r.port is None


class TestOutboundRule:
    def test_valid_rule(self):
        r = OutboundRule(vlan_id=10, dest="8.8.8.8", proto="udp", port=53, action="accept")
        assert r.dest == "8.8.8.8"

    def test_empty_dest_is_valid(self):
        r = OutboundRule(vlan_id=0)
        assert r.dest == ""

    def test_dest_accepts_cidr(self):
        r = OutboundRule(dest="10.0.0.0/8")
        assert r.dest == "10.0.0.0/8"

    def test_invalid_dest_rejected(self):
        with pytest.raises(ValidationError, match="Invalid destination CIDR"):
            OutboundRule(dest="not-an-ip")

    def test_invalid_proto_rejected(self):
        with pytest.raises(ValidationError, match="tcp, udp, any, or icmp"):
            OutboundRule(proto="sctp")

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError, match="accept or drop"):
            OutboundRule(action="reject")

    def test_port_out_of_range_rejected(self):
        with pytest.raises(ValidationError, match="between 1 and 65535"):
            OutboundRule(port=70000)

    def test_vlan_id_zero_means_all_vlans(self):
        r = OutboundRule()
        assert r.vlan_id == 0


class TestIcmpFirewallRule:
    def test_icmp_proto_with_named_type(self):
        r = InboundRule(proto="icmp", icmp_type="echo-request")
        assert r.icmp_type == "echo-request"

    def test_icmp_proto_with_numeric_type(self):
        r = InboundRule(proto="icmp", icmp_type="8")
        assert r.icmp_type == "8"

    def test_icmp_type_out_of_range_rejected(self):
        with pytest.raises(ValidationError, match="between 0 and 255"):
            InboundRule(proto="icmp", icmp_type="256")

    def test_icmp_type_unknown_name_rejected(self):
        with pytest.raises(ValidationError, match="icmp_type must be"):
            InboundRule(proto="icmp", icmp_type="; rm -rf /")

    def test_icmp_type_empty_is_none(self):
        r = InboundRule(proto="icmp", icmp_type="")
        assert r.icmp_type is None

    def test_icmp_code_in_range(self):
        r = InboundRule(proto="icmp", icmp_type="destination-unreachable", icmp_code=3)
        assert r.icmp_code == 3

    def test_icmp_code_out_of_range_rejected(self):
        with pytest.raises(ValidationError, match="between 0 and 255"):
            InboundRule(proto="icmp", icmp_code=256)

    def test_icmp_code_negative_rejected(self):
        with pytest.raises(ValidationError, match="between 0 and 255"):
            InboundRule(proto="icmp", icmp_code=-1)

    def test_icmp_type_none_by_default(self):
        r = InboundRule(proto="icmp")
        assert r.icmp_type is None
        assert r.icmp_code is None

    def test_outbound_icmp_rule(self):
        r = OutboundRule(proto="icmp", icmp_type="echo-request")
        assert r.proto == "icmp"


class TestTailscaleConfig:
    def test_valid_config(self):
        t = TailscaleConfig(enabled=True, advertise_routes=["192.168.10.0/24"])
        assert len(t.advertise_routes) == 1

    def test_invalid_route_cidr(self):
        with pytest.raises(ValidationError, match="Invalid route CIDR"):
            TailscaleConfig(enabled=True, advertise_routes=["not-a-cidr"])

    def test_empty_routes_valid(self):
        t = TailscaleConfig(enabled=False)
        assert t.advertise_routes == []


class TestSyslogConfig:
    def test_disabled_defaults(self):
        s = SyslogConfig()
        assert s.enabled is False
        assert s.server == ""

    def test_disabled_empty_server_is_valid(self):
        # server is only validated when enabled — disabled state doesn't
        # need a server configured yet.
        s = SyslogConfig(enabled=False, server="")
        assert s.server == ""

    def test_enabled_requires_server(self):
        with pytest.raises(ValidationError, match="server must be"):
            SyslogConfig(enabled=True, server="")

    def test_enabled_with_valid_hostname(self):
        s = SyslogConfig(enabled=True, server="logs.example.com")
        assert s.server == "logs.example.com"

    def test_enabled_with_valid_ip(self):
        s = SyslogConfig(enabled=True, server="10.0.0.5")
        assert s.server == "10.0.0.5"

    def test_invalid_port_rejected(self):
        with pytest.raises(ValidationError, match="between 1 and 65535"):
            SyslogConfig(port=0)

    def test_invalid_protocol_rejected(self):
        with pytest.raises(ValidationError, match="udp, tcp, or tls"):
            SyslogConfig(protocol="ssl")

    def test_valid_protocols_accepted(self):
        for proto in ("udp", "tcp", "tls"):
            assert SyslogConfig(protocol=proto).protocol == proto

    def test_invalid_facility_rejected(self):
        with pytest.raises(ValidationError, match="facility must be one of"):
            SyslogConfig(facility="; rm -rf /")

    def test_invalid_severity_rejected(self):
        with pytest.raises(ValidationError, match="severity must be one of"):
            SyslogConfig(severity="critical!!")

    def test_valid_facility_severity_accepted(self):
        s = SyslogConfig(facility="local0", severity="err")
        assert s.facility == "local0"
        assert s.severity == "err"

    def test_keep_local_defaults_true(self):
        assert SyslogConfig().keep_local is True


class TestSnmpConfig:
    def test_disabled_defaults(self):
        s = SnmpConfig()
        assert s.enabled is False
        assert s.community_ro == ""

    def test_enabled_requires_community_ro(self):
        with pytest.raises(ValidationError, match="community_ro is required"):
            SnmpConfig(enabled=True, community_ro="")

    def test_enabled_with_community_ro(self):
        s = SnmpConfig(enabled=True, community_ro="public")
        assert s.community_ro == "public"

    def test_invalid_version_rejected(self):
        with pytest.raises(ValidationError, match="v2c"):
            SnmpConfig(version="v3")

    def test_community_with_space_rejected(self):
        with pytest.raises(ValidationError, match="printable, non-whitespace"):
            SnmpConfig(community_ro="has space")

    def test_community_too_long_rejected(self):
        with pytest.raises(ValidationError):
            SnmpConfig(community_ro="a" * 33)

    def test_masked_sentinel_passes_field_validation(self):
        # The sentinel itself must satisfy _valid_community — the router
        # layer is what decides whether to treat it specially on write.
        s = SnmpConfig(community_ro=SNMP_MASKED_SENTINEL)
        assert s.community_ro == SNMP_MASKED_SENTINEL

    def test_allowlist_accepts_ip_and_cidr(self):
        s = SnmpConfig(allowlist=["10.0.0.5", "192.168.10.0/24"])
        assert len(s.allowlist) == 2

    def test_allowlist_rejects_invalid_entry(self):
        with pytest.raises(ValidationError, match="Invalid allowlist entry"):
            SnmpConfig(allowlist=["not-an-ip"])

    def test_bind_interface_validated(self):
        with pytest.raises(ValidationError, match="Invalid interface name"):
            SnmpConfig(bind_interface="eth0; rm -rf /")

    def test_empty_bind_interface_valid(self):
        s = SnmpConfig(bind_interface="")
        assert s.bind_interface == ""

    def test_location_contact_newline_rejected(self):
        with pytest.raises(ValidationError, match="newlines"):
            SnmpConfig(location="Server Room\nEvil")
