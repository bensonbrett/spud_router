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
    RouterConfig,
    StaticRoute,
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
        with pytest.raises(ValidationError, match="tcp, udp, or any"):
            InboundRule(proto="icmp")

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
