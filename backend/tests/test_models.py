# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Tests for models.py — Pydantic validation.

These tests verify that invalid input is rejected with meaningful errors
before it reaches the config generators or gets persisted to state.json.
"""
import pytest
from pydantic import ValidationError

from models import (
    DhcpReservation,
    DnsEntry,
    InboundRule,
    InterVlanRule,
    NEBULA_MASKED_SENTINEL,
    NebulaConfig,
    NebulaCredentialsRequest,
    NebulaFirewallRule,
    OutboundRule,
    PortForward,
    RouterConfig,
    SnmpConfig,
    SNMP_MASKED_SENTINEL,
    StaticRoute,
    SyslogConfig,
    TailscaleConfig,
    TlsRegenerateRequest,
    TlsUploadRequest,
    VlanConfig,
    WG_MASKED_SENTINEL,
    WireguardConfig,
    WireguardPeer,
    WireguardPeerCreateRequest,
)

_NEBULA_CERT = "-----BEGIN NEBULA CERTIFICATE-----\nabc\n-----END NEBULA CERTIFICATE-----\n"
_NEBULA_KEY  = "-----BEGIN NEBULA ED25519 PRIVATE KEY-----\nabc\n-----END NEBULA ED25519 PRIVATE KEY-----\n"


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

    def test_dhcp_reservations_defaults_empty(self):
        v = VlanConfig(vlan_id=10, name="X", interface="eth0",
                        ip_address="192.168.1.1", prefix_len=24)
        assert v.dhcp_reservations == []

    def test_dhcp_reservations_accepts_nested_list(self):
        v = VlanConfig(vlan_id=10, name="X", interface="eth0",
                        ip_address="192.168.1.1", prefix_len=24,
                        dhcp_reservations=[{"mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.50"}])
        assert v.dhcp_reservations[0].mac == "aa:bb:cc:dd:ee:ff"


class TestDhcpReservation:
    def test_valid_reservation(self):
        r = DhcpReservation(mac="aa:bb:cc:dd:ee:ff", ip="192.168.10.50")
        assert r.mac == "aa:bb:cc:dd:ee:ff"
        assert r.ip == "192.168.10.50"

    def test_mac_uppercase_normalized_to_lowercase(self):
        r = DhcpReservation(mac="AA:BB:CC:DD:EE:FF", ip="192.168.10.50")
        assert r.mac == "aa:bb:cc:dd:ee:ff"

    def test_mac_hyphen_form_normalized_to_colon(self):
        r = DhcpReservation(mac="AA-BB-CC-DD-EE-FF", ip="192.168.10.50")
        assert r.mac == "aa:bb:cc:dd:ee:ff"

    def test_mac_missing_octet_rejected(self):
        with pytest.raises(ValidationError, match="Invalid MAC"):
            DhcpReservation(mac="aa:bb:cc:dd:ee", ip="192.168.10.50")

    def test_mac_bad_chars_rejected(self):
        with pytest.raises(ValidationError, match="Invalid MAC"):
            DhcpReservation(mac="zz:bb:cc:dd:ee:ff", ip="192.168.10.50")

    def test_mac_mixed_separators_normalized_to_colons(self):
        # The regex allows each octet pair to use either separator
        # independently; normalization still collapses everything to colons.
        r = DhcpReservation(mac="aa:bb-cc:dd:ee:ff", ip="192.168.10.50")
        assert r.mac == "aa:bb:cc:dd:ee:ff"

    def test_invalid_ip_rejected(self):
        with pytest.raises(ValidationError, match="Invalid IP"):
            DhcpReservation(mac="aa:bb:cc:dd:ee:ff", ip="999.999.999.999")

    def test_hostname_optional_defaults_empty(self):
        r = DhcpReservation(mac="aa:bb:cc:dd:ee:ff", ip="192.168.10.50")
        assert r.hostname == ""

    def test_hostname_valid_accepted(self):
        r = DhcpReservation(mac="aa:bb:cc:dd:ee:ff", ip="192.168.10.50", hostname="printer")
        assert r.hostname == "printer"

    def test_hostname_invalid_rejected(self):
        with pytest.raises(ValidationError, match="Invalid hostname"):
            DhcpReservation(mac="aa:bb:cc:dd:ee:ff", ip="192.168.10.50", hostname="bad host!")

    def test_hostname_trailing_newline_rejected(self):
        # A trailing newline must not slip through into the generated
        # dnsmasq dhcp-host= line (regex uses fullmatch, not match/$).
        with pytest.raises(ValidationError, match="Invalid hostname"):
            DhcpReservation(mac="aa:bb:cc:dd:ee:ff", ip="192.168.10.50", hostname="printer\n")

    def test_description_too_long_rejected(self):
        with pytest.raises(ValidationError, match="100 characters"):
            DhcpReservation(mac="aa:bb:cc:dd:ee:ff", ip="192.168.10.50", description="x" * 101)

    def test_description_newline_rejected(self):
        with pytest.raises(ValidationError, match="newlines"):
            DhcpReservation(mac="aa:bb:cc:dd:ee:ff", ip="192.168.10.50", description="line1\nline2")

    def test_id_defaults_empty(self):
        r = DhcpReservation(mac="aa:bb:cc:dd:ee:ff", ip="192.168.10.50")
        assert r.id == ""


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

    def test_mgmt_ip_must_be_valid_ipv4(self):
        # mgmt_ip is embedded in the root-run iptables script (#164) — a
        # non-IP value must be rejected so nothing shell-injectable reaches it.
        with pytest.raises(ValidationError, match="Invalid IP"):
            RouterConfig(wan_interface="eth1", wan_mode="dhcp",
                         mgmt_ip="1.1.1.1; reboot")
        with pytest.raises(ValidationError, match="Invalid IP"):
            RouterConfig(wan_interface="eth1", wan_mode="dhcp",
                         mgmt_dhcp_start="not-an-ip")

    def test_mgmt_ip_valid_accepted(self):
        r = RouterConfig(wan_interface="eth1", wan_mode="dhcp",
                         mgmt_ip="10.0.0.1")
        assert r.mgmt_ip == "10.0.0.1"

    def test_wan_dns_mode_doh_accepted(self):
        r = RouterConfig(wan_interface="eth1", wan_mode="dhcp", wan_dns_mode="doh")
        assert r.wan_dns_mode == "doh"

    def test_wan_dns_mode_invalid_rejected(self):
        with pytest.raises(ValidationError, match="auto.*manual.*doh"):
            RouterConfig(wan_interface="eth1", wan_mode="dhcp", wan_dns_mode="dot")

    def test_doh_provider_defaults_cloudflare(self):
        r = RouterConfig(wan_interface="eth1", wan_mode="dhcp")
        assert r.doh_provider == "cloudflare"

    def test_doh_provider_whitelist(self):
        for provider in ("cloudflare", "quad9", "google"):
            r = RouterConfig(wan_interface="eth1", wan_mode="dhcp", doh_provider=provider)
            assert r.doh_provider == provider

    def test_doh_provider_invalid_rejected(self):
        with pytest.raises(ValidationError, match="cloudflare, quad9, google, or custom"):
            RouterConfig(wan_interface="eth1", wan_mode="dhcp", doh_provider="opendns")

    def test_doh_custom_requires_url(self):
        with pytest.raises(ValidationError, match="doh_custom_url is required"):
            RouterConfig(wan_interface="eth1", wan_mode="dhcp", doh_provider="custom")

    def test_doh_custom_url_valid(self):
        r = RouterConfig(wan_interface="eth1", wan_mode="dhcp", doh_provider="custom",
                          doh_custom_url="https://doh.example.com/dns-query")
        assert r.doh_custom_url == "https://doh.example.com/dns-query"

    def test_doh_custom_url_rejects_non_https(self):
        with pytest.raises(ValidationError, match="https://"):
            RouterConfig(wan_interface="eth1", wan_mode="dhcp", doh_provider="custom",
                         doh_custom_url="http://doh.example.com/dns-query")

    def test_doh_custom_url_rejects_shell_metacharacters(self):
        with pytest.raises(ValidationError):
            RouterConfig(wan_interface="eth1", wan_mode="dhcp", doh_provider="custom",
                         doh_custom_url="https://doh.example.com/dns-query; rm -rf /")

    def test_doh_custom_url_rejects_bad_host(self):
        with pytest.raises(ValidationError, match="invalid host"):
            RouterConfig(wan_interface="eth1", wan_mode="dhcp", doh_provider="custom",
                         doh_custom_url="https://bad_-host!/dns-query")

    def test_doh_custom_url_rejects_userinfo(self):
        with pytest.raises(ValidationError, match="userinfo"):
            RouterConfig(wan_interface="eth1", wan_mode="dhcp", doh_provider="custom",
                         doh_custom_url="https://user:pass@doh.example.com/dns-query")

    def test_block_wan_dns_defaults_false(self):
        r = RouterConfig(wan_interface="eth1", wan_mode="dhcp")
        assert r.block_wan_dns is False

    def test_block_wan_dns_independent_of_doh_mode(self):
        """block_wan_dns is an independent toggle — settable even outside
        doh mode; the generator (not the model) decides when to honor it."""
        r = RouterConfig(wan_interface="eth1", wan_mode="dhcp",
                          wan_dns_mode="manual", block_wan_dns=True)
        assert r.block_wan_dns is True


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


class TestPortForward:
    def test_valid_forward(self):
        pf = PortForward(proto="tcp", wan_port=8443, lan_host="192.168.10.50", lan_port=443)
        assert pf.wan_port == 8443
        assert pf.enabled is True

    def test_proto_any_rejected(self):
        with pytest.raises(ValidationError, match="tcp or udp"):
            PortForward(proto="any", wan_port=80, lan_host="192.168.10.1", lan_port=80)

    def test_proto_icmp_rejected(self):
        with pytest.raises(ValidationError, match="tcp or udp"):
            PortForward(proto="icmp", wan_port=80, lan_host="192.168.10.1", lan_port=80)

    def test_wan_port_out_of_range_rejected(self):
        with pytest.raises(ValidationError, match="between 1 and 65535"):
            PortForward(proto="tcp", wan_port=70000, lan_host="192.168.10.1", lan_port=80)

    def test_wan_port_zero_rejected(self):
        with pytest.raises(ValidationError, match="between 1 and 65535"):
            PortForward(proto="tcp", wan_port=0, lan_host="192.168.10.1", lan_port=80)

    def test_lan_port_out_of_range_rejected(self):
        with pytest.raises(ValidationError, match="between 1 and 65535"):
            PortForward(proto="tcp", wan_port=80, lan_host="192.168.10.1", lan_port=0)

    def test_invalid_lan_host_rejected(self):
        with pytest.raises(ValidationError, match="Invalid LAN host"):
            PortForward(proto="tcp", wan_port=80, lan_host="not-an-ip", lan_port=80)

    def test_lan_host_cidr_rejected(self):
        # A CIDR is not a valid DNAT destination host — only a bare IPv4 address.
        with pytest.raises(ValidationError, match="Invalid LAN host"):
            PortForward(proto="tcp", wan_port=80, lan_host="192.168.10.0/24", lan_port=80)

    def test_description_too_long_rejected(self):
        with pytest.raises(ValidationError, match="100 characters"):
            PortForward(proto="tcp", wan_port=80, lan_host="192.168.10.1", lan_port=80,
                        description="x" * 101)

    def test_description_newline_rejected(self):
        with pytest.raises(ValidationError, match="newlines"):
            PortForward(proto="tcp", wan_port=80, lan_host="192.168.10.1", lan_port=80,
                        description="line1\nline2")

    def test_enabled_defaults_true(self):
        pf = PortForward(proto="tcp", wan_port=80, lan_host="192.168.10.1", lan_port=80)
        assert pf.enabled is True

    def test_can_be_disabled(self):
        pf = PortForward(proto="udp", wan_port=51820, lan_host="192.168.10.5",
                          lan_port=51820, enabled=False)
        assert pf.enabled is False


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


class TestTlsUploadRequest:
    def test_valid_shaped_pair(self):
        r = TlsUploadRequest(
            cert_pem="-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n",
            key_pem="-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
        )
        assert "BEGIN CERTIFICATE" in r.cert_pem

    def test_cert_missing_pem_marker_rejected(self):
        with pytest.raises(ValidationError, match="PEM certificate"):
            TlsUploadRequest(cert_pem="not a cert", key_pem="-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")

    def test_key_missing_pem_marker_rejected(self):
        with pytest.raises(ValidationError, match="PEM private key"):
            TlsUploadRequest(cert_pem="-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n", key_pem="not a key")

    def test_oversized_cert_rejected(self):
        with pytest.raises(ValidationError, match="too large"):
            TlsUploadRequest(
                cert_pem="-----BEGIN CERTIFICATE-----\n" + ("a" * 40_000),
                key_pem="-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
            )

    def test_rsa_private_key_marker_accepted(self):
        # Traditional (non-PKCS8) RSA keys use "RSA PRIVATE KEY", which still
        # contains the "PRIVATE KEY-----" substring the validator checks for.
        r = TlsUploadRequest(
            cert_pem="-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n",
            key_pem="-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n",
        )
        assert "RSA PRIVATE KEY" in r.key_pem


class TestTlsRegenerateRequest:
    def test_defaults(self):
        r = TlsRegenerateRequest()
        assert r.common_name == "spud-router"
        assert r.san == []

    def test_common_name_rejects_shell_metacharacters(self):
        with pytest.raises(ValidationError):
            TlsRegenerateRequest(common_name="spud; rm -rf /")

    def test_common_name_too_long_rejected(self):
        with pytest.raises(ValidationError, match="1-64"):
            TlsRegenerateRequest(common_name="a" * 65)

    def test_san_accepts_ip_and_hostname(self):
        r = TlsRegenerateRequest(san=["192.168.1.1", "router.lan"])
        assert len(r.san) == 2

    def test_san_rejects_invalid_entry(self):
        with pytest.raises(ValidationError, match="Invalid SAN entry"):
            TlsRegenerateRequest(san=["not a valid san!"])
def _fake_wg_key() -> str:
    import base64, os
    return base64.b64encode(os.urandom(32)).decode()


class TestWireguardPeer:
    def test_valid_peer(self):
        p = WireguardPeer(public_key=_fake_wg_key(), allowed_ips=["10.100.0.2/32"])
        assert p.id == ""

    def test_invalid_public_key_shape_rejected(self):
        with pytest.raises(ValidationError, match="44-character base64"):
            WireguardPeer(public_key="too-short")

    def test_invalid_allowed_ips_rejected(self):
        with pytest.raises(ValidationError, match="Invalid allowed_ips"):
            WireguardPeer(public_key=_fake_wg_key(), allowed_ips=["not-an-ip"])

    def test_endpoint_requires_host_and_port(self):
        with pytest.raises(ValidationError, match="host:port"):
            WireguardPeer(public_key=_fake_wg_key(), endpoint="no-port-here")

    def test_endpoint_rejects_bad_port(self):
        with pytest.raises(ValidationError):
            WireguardPeer(public_key=_fake_wg_key(), endpoint="example.com:999999")

    def test_valid_endpoint_accepted(self):
        p = WireguardPeer(public_key=_fake_wg_key(), endpoint="vpn.example.com:51820")
        assert p.endpoint == "vpn.example.com:51820"

    def test_keepalive_out_of_range_rejected(self):
        with pytest.raises(ValidationError, match="65535"):
            WireguardPeer(public_key=_fake_wg_key(), persistent_keepalive=99999)

    def test_name_newline_rejected(self):
        with pytest.raises(ValidationError, match="newlines"):
            WireguardPeer(public_key=_fake_wg_key(), name="laptop\nevil")


class TestWireguardConfig:
    def test_defaults(self):
        c = WireguardConfig()
        assert c.enabled is False
        assert c.mode == "server"
        assert c.listen_port == 51820
        assert c.private_key == ""

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValidationError, match="server.*client"):
            WireguardConfig(mode="bogus")

    def test_invalid_listen_port_rejected(self):
        with pytest.raises(ValidationError, match="between 1 and 65535"):
            WireguardConfig(listen_port=0)

    def test_masked_sentinel_accepted_for_private_key(self):
        c = WireguardConfig(private_key=WG_MASKED_SENTINEL)
        assert c.private_key == WG_MASKED_SENTINEL

    def test_invalid_private_key_shape_rejected(self):
        with pytest.raises(ValidationError, match="44-character base64"):
            WireguardConfig(private_key="not-a-real-key")

    def test_empty_private_key_valid(self):
        c = WireguardConfig(private_key="")
        assert c.private_key == ""

    def test_invalid_address_rejected(self):
        with pytest.raises(ValidationError, match="valid IP/CIDR"):
            WireguardConfig(address="not-an-address")

    def test_valid_address_accepted(self):
        c = WireguardConfig(address="10.100.0.1/24")
        assert c.address == "10.100.0.1/24"


class TestWireguardPeerCreateRequest:
    def test_with_public_key_no_client_address_needed(self):
        r = WireguardPeerCreateRequest(public_key=_fake_wg_key(), allowed_ips=["10.100.0.2/32"])
        assert r.client_address is None

    def test_without_public_key_requires_client_address(self):
        with pytest.raises(ValidationError, match="client_address is required"):
            WireguardPeerCreateRequest(name="laptop")

    def test_without_public_key_with_client_address_ok(self):
        r = WireguardPeerCreateRequest(name="laptop", client_address="10.100.0.2/32")
        assert r.public_key is None

    def test_invalid_client_address_rejected(self):
        with pytest.raises(ValidationError, match="client_address must be"):
            WireguardPeerCreateRequest(client_address="not-an-address")


class TestNebulaFirewallRule:
    def test_defaults_allow_any(self):
        r = NebulaFirewallRule()
        assert r.port == "any" and r.proto == "any" and r.host == "any"

    def test_numeric_port_accepted(self):
        assert NebulaFirewallRule(port="22").port == "22"

    def test_port_range_accepted(self):
        assert NebulaFirewallRule(port="1000-2000").port == "1000-2000"

    def test_invalid_port_rejected(self):
        with pytest.raises(ValidationError, match="port must be"):
            NebulaFirewallRule(port="not-a-port")

    def test_port_out_of_range_rejected(self):
        with pytest.raises(ValidationError, match="port must be"):
            NebulaFirewallRule(port="99999")

    def test_invalid_proto_rejected(self):
        with pytest.raises(ValidationError, match="proto must be"):
            NebulaFirewallRule(proto="ftp")

    def test_valid_host_ip_accepted(self):
        assert NebulaFirewallRule(host="192.168.100.5").host == "192.168.100.5"

    def test_invalid_host_rejected(self):
        with pytest.raises(ValidationError, match="host must be"):
            NebulaFirewallRule(host="not-an-ip")


class TestNebulaConfig:
    def test_defaults(self):
        c = NebulaConfig()
        assert c.enabled is False
        assert c.listen_port == 4242
        assert c.lighthouse_hosts == []
        assert c.static_host_map == {}
        assert c.firewall_outbound == [NebulaFirewallRule(port="any", proto="any", host="any")]
        assert c.firewall_inbound == []

    def test_invalid_listen_port_rejected(self):
        with pytest.raises(ValidationError, match="between 1 and 65535"):
            NebulaConfig(listen_port=0)

    def test_invalid_lighthouse_host_rejected(self):
        with pytest.raises(ValidationError, match="lighthouse_hosts"):
            NebulaConfig(lighthouse_hosts=["not-an-ip"])

    def test_valid_lighthouse_host_accepted(self):
        c = NebulaConfig(lighthouse_hosts=["192.168.100.1"])
        assert c.lighthouse_hosts == ["192.168.100.1"]

    def test_static_host_map_bad_key_rejected(self):
        with pytest.raises(ValidationError, match="static_host_map key"):
            NebulaConfig(static_host_map={"not-an-ip": ["host:1234"]})

    def test_static_host_map_bad_endpoint_rejected(self):
        with pytest.raises(ValidationError, match="host:port"):
            NebulaConfig(static_host_map={"192.168.100.1": ["no-port-here"]})

    def test_static_host_map_bad_port_rejected(self):
        with pytest.raises(ValidationError, match="1-65535"):
            NebulaConfig(static_host_map={"192.168.100.1": ["host:999999"]})

    def test_valid_static_host_map_accepted(self):
        c = NebulaConfig(static_host_map={"192.168.100.1": ["lh.example.com:4242"]})
        assert c.static_host_map == {"192.168.100.1": ["lh.example.com:4242"]}

    def test_empty_cert_pem_valid(self):
        assert NebulaConfig(cert_pem="").cert_pem == ""

    def test_invalid_cert_pem_shape_rejected(self):
        with pytest.raises(ValidationError, match="cert_pem must be PEM-formatted"):
            NebulaConfig(cert_pem="not-pem-at-all")

    def test_invalid_ca_pem_shape_rejected(self):
        with pytest.raises(ValidationError, match="ca_pem must be PEM-formatted"):
            NebulaConfig(ca_pem="not-pem-at-all")

    def test_masked_sentinel_accepted_for_key_pem(self):
        c = NebulaConfig(key_pem=NEBULA_MASKED_SENTINEL)
        assert c.key_pem == NEBULA_MASKED_SENTINEL

    def test_invalid_key_pem_shape_rejected(self):
        with pytest.raises(ValidationError, match="key_pem must be PEM-formatted"):
            NebulaConfig(key_pem="not-pem-at-all")

    def test_valid_cert_and_key_pem_accepted(self):
        c = NebulaConfig(cert_pem=_NEBULA_CERT, key_pem=_NEBULA_KEY, ca_pem=_NEBULA_CERT)
        assert c.cert_pem == _NEBULA_CERT
        assert c.key_pem == _NEBULA_KEY


class TestNebulaCredentialsRequest:
    def test_valid_triple_accepted(self):
        r = NebulaCredentialsRequest(cert_pem=_NEBULA_CERT, key_pem=_NEBULA_KEY, ca_pem=_NEBULA_CERT)
        assert r.cert_pem == _NEBULA_CERT

    def test_sentinel_not_accepted_for_key_pem(self):
        """Unlike NebulaConfig (which allows the sentinel to mean 'keep
        unchanged' on a settings PUT), a credentials import always expects
        a real key — the router's own PUT handler is what enforces the
        'preserve existing' semantics, not this request model."""
        with pytest.raises(ValidationError, match="key_pem must be PEM-formatted"):
            NebulaCredentialsRequest(cert_pem=_NEBULA_CERT, key_pem=NEBULA_MASKED_SENTINEL, ca_pem=_NEBULA_CERT)

    def test_missing_field_rejected(self):
        with pytest.raises(ValidationError):
            NebulaCredentialsRequest(cert_pem=_NEBULA_CERT, key_pem=_NEBULA_KEY)
