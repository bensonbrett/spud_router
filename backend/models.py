# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Pydantic models for request/response validation.
All models used across the application live here to avoid circular imports.
"""
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator
import ipaddress
import re
import urllib.parse


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# MAC addresses are accepted in either colon or hyphen form, with either
# case, and normalized to lowercase colon-separated form before storage —
# so two reservations written as "AA-BB-CC-DD-EE-FF" and "aa:bb:cc:dd:ee:ff"
# are recognized as the same address by the per-VLAN uniqueness check in
# routers/network.py.
_MAC_RE = re.compile(r'^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$')


def _valid_mac(v: str) -> str:
    if not _MAC_RE.match(v):
        raise ValueError(f"Invalid MAC address: {v}")
    return v.lower().replace("-", ":")


class DhcpReservation(BaseModel):
    id: str = ""
    mac: str
    ip: str
    hostname: str = ""
    description: str = ""

    @field_validator("mac")
    @classmethod
    def valid_mac(cls, v: str) -> str:
        return _valid_mac(v)

    @field_validator("ip")
    @classmethod
    def valid_ip(cls, v: str) -> str:
        try:
            ipaddress.IPv4Address(v)
        except ValueError:
            raise ValueError(f"Invalid IP address: {v}")
        return v

    @field_validator("hostname")
    @classmethod
    def valid_hostname(cls, v: str) -> str:
        if not v:
            return v
        # fullmatch (not match) so a trailing newline can't slip through the
        # `$` anchor and land in the generated dnsmasq dhcp-host= line.
        if not re.fullmatch(r'[a-zA-Z0-9]([a-zA-Z0-9._-]{0,61}[a-zA-Z0-9])?', v):
            raise ValueError(f"Invalid hostname: {v}")
        return v

    @field_validator("description")
    @classmethod
    def valid_description(cls, v: str) -> str:
        if len(v) > 100:
            raise ValueError("description must be 100 characters or fewer")
        if "\n" in v or "\r" in v:
            raise ValueError("description must not contain newlines")
        return v


class VlanConfig(BaseModel):
    vlan_id: int
    name: str
    interface: str
    ip_address: str
    prefix_len: int
    dhcp_enabled: bool = True
    dhcp_start: str = ""
    dhcp_end: str = ""
    dhcp_lease: str = "12h"
    isolate: bool = False
    dns_server: str = ""           # override DHCP option 6; empty = gateway (self)
    dhcp_options: list[str] = []   # extra raw dnsmasq dhcp-option values, e.g. "42,192.168.10.1"
    icmp_echo: bool = False        # allow inbound ping (ICMP echo-request) on this VLAN; blocked by default
    dhcp_reservations: list[DhcpReservation] = []   # per-VLAN MAC→IP DHCP pinning

    @field_validator("vlan_id")
    @classmethod
    def vlan_id_range(cls, v: int) -> int:
        if not 1 <= v <= 4094:
            raise ValueError("VLAN ID must be between 1 and 4094")
        return v

    @field_validator("ip_address", "dns_server")
    @classmethod
    def valid_ip(cls, v: str) -> str:
        if not v:  # Allow empty IP for WAN VLANs / unset DNS override
            return v
        try:
            ipaddress.IPv4Address(v)
        except ValueError:
            raise ValueError(f"Invalid IP address: {v}")
        return v

    @field_validator("prefix_len")
    @classmethod
    def valid_prefix(cls, v: int) -> int:
        if not 1 <= v <= 30:
            raise ValueError("Prefix length must be between 1 and 30")
        return v

    @field_validator("interface")
    @classmethod
    def valid_interface(cls, v: str) -> str:
        if not re.match(r'^[a-zA-Z0-9_-]{1,15}$', v):
            raise ValueError(f"Invalid interface name: {v}")
        return v

    @field_validator("dhcp_options")
    @classmethod
    def valid_dhcp_options(cls, v: list[str]) -> list[str]:
        for opt in v:
            if len(opt) > 200:
                raise ValueError("dhcp_options entries must be 200 characters or fewer")
            if "\n" in opt or "\r" in opt:
                raise ValueError("dhcp_options entries must not contain newlines")
        return v


class RouterConfig(BaseModel):
    wan_interface: str
    wan_mode: str                         # "dhcp" | "static"
    wan_ip: Optional[str] = None
    wan_prefix: Optional[int] = None
    wan_gateway: Optional[str] = None
    wan_dns_mode: str = "auto"            # "auto" (from WAN DHCP) | "manual"
    wan_dns: Optional[str] = "1.1.1.1"    # primary upstream (manual mode)
    wan_dns_alt: Optional[str] = None     # optional secondary upstream (manual mode)
    hostname: str = "spud-router"
    # Management interface (untagged direct access)
    mgmt_enabled: bool = False
    mgmt_interface: str = "eth0"
    mgmt_ip: str = "192.168.1.1"
    mgmt_prefix: int = 24
    mgmt_dhcp_start: str = "192.168.1.100"
    mgmt_dhcp_end: str = "192.168.1.150"
    mgmt_dhcp_lease: str = "12h"
    mgmt_icmp_echo: bool = False    # allow inbound ping on the management interface; blocked by default
    # DNS-over-HTTPS upstream (encrypts the router's own upstream DNS via a
    # local dnsproxy instance). Independent of block_wan_dns — DoH can be on
    # without blocking plaintext :53 from LAN clients.
    doh_provider: str = "cloudflare"        # "cloudflare" | "quad9" | "google" | "custom"
    doh_custom_url: Optional[str] = None    # required when doh_provider == "custom"
    block_wan_dns: bool = False             # block LAN plaintext :53 to WAN; default off

    @field_validator("wan_interface")
    @classmethod
    def valid_wan_interface(cls, v: str) -> str:
        # Allow dots for VLAN subinterfaces (e.g. eth0.2)
        if not re.match(r'^[a-zA-Z0-9_.-]{1,15}$', v):
            raise ValueError(f"Invalid interface name: {v}")
        return v

    @field_validator("mgmt_interface")
    @classmethod
    def valid_mgmt_interface(cls, v: str) -> str:
        if not re.match(r'^[a-zA-Z0-9_-]{1,15}$', v):
            raise ValueError(f"Invalid interface name: {v}")
        return v

    @field_validator("wan_mode")
    @classmethod
    def valid_wan_mode(cls, v: str) -> str:
        if v not in ("dhcp", "static"):
            raise ValueError("wan_mode must be 'dhcp' or 'static'")
        return v

    @field_validator("wan_ip", "wan_gateway", "wan_dns", "wan_dns_alt")
    @classmethod
    def valid_optional_ip(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        try:
            ipaddress.IPv4Address(v)
        except ValueError:
            raise ValueError(f"Invalid IP address: {v}")
        return v

    @field_validator("wan_dns_mode")
    @classmethod
    def valid_wan_dns_mode(cls, v: str) -> str:
        if v not in ("auto", "manual", "doh"):
            raise ValueError("wan_dns_mode must be 'auto', 'manual', or 'doh'")
        return v

    @field_validator("wan_prefix")
    @classmethod
    def valid_wan_prefix(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not 1 <= v <= 30:
            raise ValueError("wan_prefix must be between 1 and 30")
        return v

    @field_validator("hostname")
    @classmethod
    def valid_hostname(cls, v: str) -> str:
        if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$', v):
            raise ValueError(f"Invalid hostname: {v}")
        return v

    @field_validator("doh_provider")
    @classmethod
    def valid_doh_provider(cls, v: str) -> str:
        if v not in ("cloudflare", "quad9", "google", "custom"):
            raise ValueError("doh_provider must be cloudflare, quad9, google, or custom")
        return v

    @field_validator("doh_custom_url")
    @classmethod
    def valid_doh_custom_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        v = v.strip()
        if len(v) > 253 or not v.isascii() or not v.isprintable() or " " in v:
            raise ValueError("doh_custom_url must be a well-formed https:// URL")
        parsed = urllib.parse.urlparse(v)
        if parsed.scheme != "https":
            raise ValueError("doh_custom_url must use https://")
        if parsed.username or parsed.password:
            raise ValueError("doh_custom_url must not contain userinfo")
        host = parsed.hostname
        if not host:
            raise ValueError("doh_custom_url must include a host")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not _HOSTNAME_RE.match(host):
                raise ValueError(f"doh_custom_url has an invalid host: {host}")
        return v

    @model_validator(mode="after")
    def valid_doh_custom_url_when_selected(self) -> "RouterConfig":
        if self.doh_provider == "custom" and not self.doh_custom_url:
            raise ValueError("doh_custom_url is required when doh_provider is 'custom'")
        return self


class StaticRoute(BaseModel):
    destination: str           # CIDR e.g. "10.0.0.0/8"
    gateway: str
    interface: Optional[str] = None
    description: str = ""

    @field_validator("destination")
    @classmethod
    def valid_cidr(cls, v: str) -> str:
        try:
            ipaddress.IPv4Network(v, strict=False)
        except ValueError:
            raise ValueError(f"Invalid CIDR: {v}")
        return v

    @field_validator("gateway")
    @classmethod
    def valid_gateway(cls, v: str) -> str:
        try:
            ipaddress.IPv4Address(v)
        except ValueError:
            raise ValueError(f"Invalid gateway IP: {v}")
        return v


class DnsEntry(BaseModel):
    hostname: str
    ip: str
    description: str = ""

    @field_validator("hostname")
    @classmethod
    def valid_hostname(cls, v: str) -> str:
        # Allow short names and FQDNs
        if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9._-]{0,61}[a-zA-Z0-9])?$', v):
            raise ValueError(f"Invalid hostname: {v}")
        return v

    @field_validator("ip")
    @classmethod
    def valid_ip(cls, v: str) -> str:
        try:
            ipaddress.IPv4Address(v)
        except ValueError:
            raise ValueError(f"Invalid IP address: {v}")
        return v


class TailscaleConfig(BaseModel):
    enabled: bool
    advertise_routes: list[str] = []
    exit_node: bool = False
    accept_routes: bool = True

    @field_validator("advertise_routes")
    @classmethod
    def valid_routes(cls, v: list[str]) -> list[str]:
        for route in v:
            try:
                ipaddress.IPv4Network(route, strict=False)
            except ValueError:
                raise ValueError(f"Invalid route CIDR: {route}")
        return v


class AuthKeyRequest(BaseModel):
    auth_key: str

    @field_validator("auth_key")
    @classmethod
    def valid_auth_key(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("tskey-") or len(v) < 10:
            raise ValueError("auth_key must be a valid tailscale key (starts with 'tskey-')")
        return v


# Whitelisted ICMP type names accepted from the UI/API — mapped straight to
# iptables' own --icmp-type tokens. Never interpolate a raw user string into
# the generated shell script; only names from this set (or an in-range int)
# are ever allowed through.
ICMP_TYPE_NAMES = ("echo-request", "echo-reply", "destination-unreachable", "time-exceeded", "any")


class BaseFirewallRule(BaseModel):
    proto: str = "any"
    port: Optional[int] = None
    action: str = "accept"        # accept | drop
    description: str = ""
    icmp_type: Optional[str] = None    # whitelisted name or numeric 0-255; only meaningful when proto="icmp"
    icmp_code: Optional[int] = None    # 0-255; only meaningful when proto="icmp"

    @field_validator("proto")
    @classmethod
    def valid_proto(cls, v: str) -> str:
        if v not in ("tcp", "udp", "any", "icmp"):
            raise ValueError("proto must be tcp, udp, any, or icmp")
        return v

    @field_validator("action")
    @classmethod
    def valid_action(cls, v: str) -> str:
        if v not in ("accept", "drop"):
            raise ValueError("action must be accept or drop")
        return v

    @field_validator("port")
    @classmethod
    def valid_port(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not 1 <= v <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return v

    @field_validator("description")
    @classmethod
    def valid_description(cls, v: str) -> str:
        # Reject newlines and limit length to prevent shell injection in generated scripts
        if len(v) > 100:
            raise ValueError("description must be 100 characters or fewer")
        if "\n" in v or "\r" in v:
            raise ValueError("description must not contain newlines")
        return v

    @field_validator("icmp_type")
    @classmethod
    def valid_icmp_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if v in ICMP_TYPE_NAMES:
            return v
        try:
            n = int(v)
        except ValueError:
            raise ValueError(f"icmp_type must be one of {ICMP_TYPE_NAMES} or an integer 0-255")
        if not 0 <= n <= 255:
            raise ValueError("icmp_type integer must be between 0 and 255")
        return str(n)

    @field_validator("icmp_code")
    @classmethod
    def valid_icmp_code(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not 0 <= v <= 255:
            raise ValueError("icmp_code must be between 0 and 255")
        return v


class InboundRule(BaseFirewallRule):
    id: str = ""
    vlan_id: int = 0              # 0 = all VLANs
    proto: str = "tcp"


class InterVlanRule(BaseFirewallRule):
    id: str = ""
    from_vlan: int = 0           # 0 = any
    to_vlan: int = 0             # 0 = any


class OutboundRule(BaseFirewallRule):
    id: str = ""
    vlan_id: int = 0          # 0 = all LAN VLANs (source)
    dest: str = ""            # destination host/CIDR; "" = any

    @field_validator("dest")
    @classmethod
    def valid_dest(cls, v: str) -> str:
        if not v:
            return v
        try:
            ipaddress.ip_network(v, strict=False)   # accepts host or CIDR
        except ValueError:
            raise ValueError(f"Invalid destination CIDR: {v}")
        return v


class PortForward(BaseModel):
    """Inbound WAN port -> LAN host DNAT rule (issue #107). Unlike
    BaseFirewallRule, proto/wan_port/lan_port/lan_host are all required —
    a port forward without a fully-specified destination is meaningless
    and would either fail to generate a working rule or (worse) generate
    an over-broad one."""
    id: str = ""
    proto: str            # "tcp" | "udp" — no "any"/"icmp": DNAT needs an explicit protocol
    wan_port: int
    lan_host: str
    lan_port: int
    description: str = ""
    enabled: bool = True

    @field_validator("proto")
    @classmethod
    def valid_proto(cls, v: str) -> str:
        if v not in ("tcp", "udp"):
            raise ValueError("proto must be tcp or udp")
        return v

    @field_validator("wan_port", "lan_port")
    @classmethod
    def valid_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return v

    @field_validator("lan_host")
    @classmethod
    def valid_lan_host(cls, v: str) -> str:
        try:
            ipaddress.IPv4Address(v)
        except ValueError:
            raise ValueError(f"Invalid LAN host IP address: {v}")
        return v

    @field_validator("description")
    @classmethod
    def valid_description(cls, v: str) -> str:
        # Reject newlines and limit length to prevent shell injection in generated scripts
        if len(v) > 100:
            raise ValueError("description must be 100 characters or fewer")
        if "\n" in v or "\r" in v:
            raise ValueError("description must not contain newlines")
        return v


class OutboundDefaultRequest(BaseModel):
    default: str   # "allow" | "deny" — fallback egress policy for LAN VLANs

    @field_validator("default")
    @classmethod
    def valid_default(cls, v: str) -> str:
        if v not in ("allow", "deny"):
            raise ValueError("default must be 'allow' or 'deny'")
        return v


class ApplyRequest(BaseModel):
    dry_run: bool = False


class ApplyConfirmRequest(BaseModel):
    token: str   # must match the token returned by the armed POST /api/apply


# RFC-1123 hostname or IPv4/IPv6 literal — no spaces, shell metacharacters,
# slashes, or command separators. This is the critical injection guard for
# DiagnosticRequest.target, which flows straight into a subprocess arg list.
_HOSTNAME_RE = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9.-]{0,253}[a-zA-Z0-9])?$')


class DiagnosticRequest(BaseModel):
    command: str    # "ping" | "traceroute" | "nslookup"
    target: str     # IPv4/IPv6 address or hostname

    @field_validator("command")
    @classmethod
    def valid_command(cls, v: str) -> str:
        if v not in ("ping", "traceroute", "nslookup"):
            raise ValueError("command must be ping, traceroute, or nslookup")
        return v

    @field_validator("target")
    @classmethod
    def valid_target(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 253:
            raise ValueError("target must be 1-253 characters")
        try:
            ipaddress.ip_address(v)
            return v
        except ValueError:
            pass
        if not _HOSTNAME_RE.match(v):
            raise ValueError("target must be a valid IP address or hostname")
        return v


# MAC address, colon- or hyphen-separated (each octet may use either
# separator, matching common copy/paste sources — arp -a, router UIs,
# device labels). Normalized to lowercase colon-separated form by
# WolRequest.valid_mac below so downstream code (the actual magic-packet
# builder in routers/diagnostics.py) only ever sees one canonical shape.
_MAC_RE = re.compile(r'^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$')


class WolRequest(BaseModel):
    """
    POST /api/diagnostics/wol body — send a Wake-on-LAN magic packet.

    A sibling endpoint (rather than a third DiagnosticRequest.command value)
    because WOL's inputs (a MAC, optionally a VLAN) don't fit
    DiagnosticRequest's single "target" string, and giving it its own model
    keeps each validator focused.

    vlan_id and broadcast are mutually exclusive ways to pick a broadcast
    domain: if vlan_id is given, the router resolves that VLAN's own
    broadcast address (so the packet is sent onto the right L2 segment);
    otherwise an explicit broadcast override may be given; if neither is
    given, the packet goes out as a global 255.255.255.255 broadcast.
    """
    mac: str
    vlan_id: Optional[int] = None
    broadcast: Optional[str] = None

    @field_validator("mac")
    @classmethod
    def valid_mac(cls, v: str) -> str:
        v = v.strip()
        if not _MAC_RE.match(v):
            raise ValueError("mac must be a MAC address like aa:bb:cc:dd:ee:ff")
        hex_only = v.replace(":", "").replace("-", "").lower()
        return ":".join(hex_only[i:i + 2] for i in range(0, 12, 2))

    @field_validator("vlan_id")
    @classmethod
    def valid_vlan_id(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not 1 <= v <= 4094:
            raise ValueError("vlan_id must be between 1 and 4094")
        return v

    @field_validator("broadcast")
    @classmethod
    def valid_broadcast(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        try:
            ipaddress.IPv4Address(v)
        except ValueError:
            raise ValueError(f"Invalid broadcast address: {v}")
        return v

    @model_validator(mode="after")
    def valid_vlan_and_broadcast_not_both(self) -> "WolRequest":
        if self.vlan_id is not None and self.broadcast is not None:
            raise ValueError("specify at most one of vlan_id or broadcast, not both")
        return self


class WirelessSsid(BaseModel):
    id: str = ""
    ssid: str
    vlan_id: int
    band: str = "2.4"               # "2.4" | "5"
    channel: str = "auto"           # "auto" | "1"-"14" | "36"-"165"
    security: str = "wpa2"         # "open" | "wpa2" | "wpa3" | "wpa2/3"
    password: str = ""
    hidden: bool = False
    enabled: bool = True

    @field_validator("band")
    @classmethod
    def valid_band(cls, v: str) -> str:
        if v not in ("2.4", "5"):
            raise ValueError("band must be '2.4' or '5'")
        return v

    @field_validator("security")
    @classmethod
    def valid_security(cls, v: str) -> str:
        if v not in ("open", "wpa2", "wpa3", "wpa2/3"):
            raise ValueError("security must be open, wpa2, wpa3, or wpa2/3")
        return v

    @field_validator("ssid")
    @classmethod
    def valid_ssid(cls, v: str) -> str:
        if not v or len(v) > 32:
            raise ValueError("SSID must be 1–32 characters")
        if not v.isprintable() or "\n" in v or "\r" in v:
            raise ValueError("SSID must contain only printable characters with no newlines")
        return v

    @field_validator("password")
    @classmethod
    def valid_password(cls, v: str) -> str:
        if not v:
            return v
        if len(v) > 63:
            raise ValueError("WPA password must be 63 characters or fewer")
        if not v.isascii() or not v.isprintable() or "\n" in v or "\r" in v:
            raise ValueError("WPA password must contain only printable ASCII characters with no newlines")
        return v


class WirelessConfig(BaseModel):
    enabled: bool = False
    interface: str = "wlan0"
    country_code: str = "US"
    ssids: list[WirelessSsid] = []

    @field_validator("country_code")
    @classmethod
    def valid_country(cls, v: str) -> str:
        if not re.match(r'^[A-Z]{2}$', v):
            raise ValueError("country_code must be a 2-letter ISO country code e.g. US, GB")
        return v

    @field_validator("interface")
    @classmethod
    def valid_interface(cls, v: str) -> str:
        if not re.match(r'^[a-zA-Z0-9_-]{1,15}$', v):
            raise ValueError(f"Invalid interface name: {v}")
        return v


# rsyslog selector tokens — whitelisted so SyslogConfig.facility/severity can
# be interpolated directly into the generated rsyslog selector line
# ("<facility>.<severity>") with no further escaping.
SYSLOG_FACILITIES = (
    "*", "kern", "user", "mail", "daemon", "auth", "syslog", "lpr", "news",
    "uucp", "cron", "authpriv", "ftp",
    "local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7",
)
SYSLOG_SEVERITIES = ("*", "emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")


class SyslogConfig(BaseModel):
    enabled: bool = False
    server: str = ""                # IPv4/IPv6/hostname; validated only when enabled
    port: int = 514
    protocol: str = "udp"           # "udp" | "tcp" | "tls"
    facility: str = "*"
    severity: str = "*"
    keep_local: bool = True

    @field_validator("port")
    @classmethod
    def valid_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return v

    @field_validator("protocol")
    @classmethod
    def valid_protocol(cls, v: str) -> str:
        if v not in ("udp", "tcp", "tls"):
            raise ValueError("protocol must be udp, tcp, or tls")
        return v

    @field_validator("facility")
    @classmethod
    def valid_facility(cls, v: str) -> str:
        if v not in SYSLOG_FACILITIES:
            raise ValueError(f"facility must be one of {SYSLOG_FACILITIES}")
        return v

    @field_validator("severity")
    @classmethod
    def valid_severity(cls, v: str) -> str:
        if v not in SYSLOG_SEVERITIES:
            raise ValueError(f"severity must be one of {SYSLOG_SEVERITIES}")
        return v

    @model_validator(mode="after")
    def valid_server_when_enabled(self) -> "SyslogConfig":
        if not self.enabled:
            return self
        v = (self.server or "").strip()
        if not v or len(v) > 253:
            raise ValueError("server must be 1-253 characters when syslog forwarding is enabled")
        try:
            ipaddress.ip_address(v)
            return self
        except ValueError:
            pass
        if not _HOSTNAME_RE.match(v):
            raise ValueError("server must be a valid IP address or hostname")
        return self


# Sentinel the API returns in place of a stored SNMP community string, and
# accepts back on PUT to mean "leave it unchanged" — the community is
# write-only from the caller's perspective (routers/snmp.py never echoes the
# real value back). "********" happens to satisfy _valid_community's own
# character/length rules, so no special-casing is needed at the model layer.
SNMP_MASKED_SENTINEL = "********"

_COMMUNITY_RE = re.compile(r'^[!-~]{1,32}$')  # printable ASCII, no whitespace, 1-32 chars


def _valid_community(v: str) -> str:
    if not _COMMUNITY_RE.match(v):
        raise ValueError("community string must be 1-32 printable, non-whitespace ASCII characters")
    return v


class SnmpConfig(BaseModel):
    enabled: bool = False
    version: str = "v2c"             # only v2c supported for now
    community_ro: str = ""           # required when enabled; write-only (see SNMP_MASKED_SENTINEL)
    community_rw: str = ""           # optional; empty = no write access configured
    allowlist: list[str] = []        # source IPs/CIDRs allowed to poll
    bind_interface: str = ""         # empty = bind all interfaces (udp:161)
    location: str = ""               # sysLocation
    contact: str = ""                # sysContact

    @field_validator("version")
    @classmethod
    def valid_version(cls, v: str) -> str:
        if v != "v2c":
            raise ValueError("version must be 'v2c' (the only version currently supported)")
        return v

    @field_validator("community_ro")
    @classmethod
    def valid_community_ro(cls, v: str) -> str:
        if not v:
            return v
        return _valid_community(v)

    @field_validator("community_rw")
    @classmethod
    def valid_community_rw(cls, v: str) -> str:
        if not v:
            return v
        return _valid_community(v)

    @field_validator("allowlist")
    @classmethod
    def valid_allowlist(cls, v: list[str]) -> list[str]:
        for entry in v:
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError:
                raise ValueError(f"Invalid allowlist entry (must be an IP or CIDR): {entry}")
        return v

    @field_validator("bind_interface")
    @classmethod
    def valid_bind_interface(cls, v: str) -> str:
        if v and not re.match(r'^[a-zA-Z0-9_-]{1,15}$', v):
            raise ValueError(f"Invalid interface name: {v}")
        return v

    @field_validator("location", "contact")
    @classmethod
    def valid_no_newlines(cls, v: str) -> str:
        if len(v) > 100:
            raise ValueError("must be 100 characters or fewer")
        if "\n" in v or "\r" in v:
            raise ValueError("must not contain newlines")
        return v

    @model_validator(mode="after")
    def valid_community_ro_when_enabled(self) -> "SnmpConfig":
        if self.enabled and not self.community_ro:
            raise ValueError("community_ro is required when SNMP is enabled")
        return self


# Generous but bounded — real PEM certs/keys are a few KB; this just guards
# against pathological request bodies before the string ever reaches openssl.
_MAX_PEM_LEN = 32_768


class TlsUploadRequest(BaseModel):
    cert_pem: str
    key_pem: str

    @field_validator("cert_pem")
    @classmethod
    def valid_cert_pem(cls, v: str) -> str:
        if len(v) > _MAX_PEM_LEN:
            raise ValueError("cert_pem is too large")
        if "-----BEGIN CERTIFICATE-----" not in v:
            raise ValueError("cert_pem does not look like a PEM certificate")
        return v

    @field_validator("key_pem")
    @classmethod
    def valid_key_pem(cls, v: str) -> str:
        if len(v) > _MAX_PEM_LEN:
            raise ValueError("key_pem is too large")
        if "PRIVATE KEY-----" not in v:
            raise ValueError("key_pem does not look like a PEM private key")
        return v


class TlsRegenerateRequest(BaseModel):
    common_name: str = "spud-router"
    san: list[str] = []   # extra Subject Alternative Names (IPs or DNS names)

    @field_validator("common_name")
    @classmethod
    def valid_common_name(cls, v: str) -> str:
        if not v or len(v) > 64:
            raise ValueError("common_name must be 1-64 characters")
        if not re.match(r'^[a-zA-Z0-9.-]+$', v):
            raise ValueError("common_name must contain only letters, digits, dots, and hyphens")
        return v

    @field_validator("san")
    @classmethod
    def valid_san(cls, v: list[str]) -> list[str]:
        for entry in v:
            try:
                ipaddress.ip_address(entry)
                continue
            except ValueError:
                pass
            if not _HOSTNAME_RE.match(entry):
                raise ValueError(f"Invalid SAN entry (must be an IP or hostname): {entry}")
        return v
# Sentinel the API returns in place of a stored WireGuard private key, and
# accepts back on PUT to mean "leave it unchanged" — same write-only
# pattern as SNMP_MASKED_SENTINEL. Unlike the SNMP community string (loose
# ASCII shape), a real WireGuard key has a strict, narrow shape (exactly
# 44 base64 characters) that "********" doesn't satisfy, so — unlike
# SNMP's community field — the sentinel needs an explicit carve-out, and
# only on private_key (WireguardConfig.valid_private_key below); public
# keys (peers' and this device's own) are never allowed to be the
# sentinel, since only a stored *private* key is ever masked.
WG_MASKED_SENTINEL = "********"

# WireGuard keys are 32-byte Curve25519 keys, base64-encoded — always
# exactly 44 characters (43 base64 chars + one "=" pad).
_WG_KEY_RE = re.compile(r'^[A-Za-z0-9+/]{43}=$')


def _valid_wg_key(v: str, field_name: str) -> str:
    if not _WG_KEY_RE.match(v):
        raise ValueError(f"{field_name} must be a 44-character base64 WireGuard key")
    return v


class WireguardPeer(BaseModel):
    id: str = ""
    name: str = ""                          # friendly label, sanitized like other free-text fields
    public_key: str
    allowed_ips: list[str] = []              # CIDRs this peer may originate/receive traffic for
    endpoint: Optional[str] = None           # "host:port" — set when this device dials out to the peer
    persistent_keepalive: Optional[int] = None  # seconds; useful when the peer is behind NAT

    @field_validator("name")
    @classmethod
    def valid_name(cls, v: str) -> str:
        if len(v) > 64:
            raise ValueError("name must be 64 characters or fewer")
        if "\n" in v or "\r" in v:
            raise ValueError("name must not contain newlines")
        return v

    @field_validator("public_key")
    @classmethod
    def valid_public_key(cls, v: str) -> str:
        return _valid_wg_key(v, "public_key")

    @field_validator("allowed_ips")
    @classmethod
    def valid_allowed_ips(cls, v: list[str]) -> list[str]:
        for entry in v:
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError:
                raise ValueError(f"Invalid allowed_ips entry (must be an IP or CIDR): {entry}")
        return v

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if v.count(":") != 1:
            raise ValueError("endpoint must be in host:port form")
        host, _, port_str = v.rpartition(":")
        try:
            port = int(port_str)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise ValueError("endpoint port must be an integer between 1 and 65535")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not _HOSTNAME_RE.match(host):
                raise ValueError(f"endpoint has an invalid host: {host}")
        return v

    @field_validator("persistent_keepalive")
    @classmethod
    def valid_keepalive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not 1 <= v <= 65535:
            raise ValueError("persistent_keepalive must be between 1 and 65535 seconds")
        return v


class WireguardConfig(BaseModel):
    enabled: bool = False
    mode: str = "server"           # "server" | "client"
    listen_port: int = 51820
    private_key: str = ""          # write-only (see WG_MASKED_SENTINEL); "" means "not yet generated"
    address: str = ""              # this device's own tunnel address, CIDR e.g. "10.100.0.1/24"
    peers: list[WireguardPeer] = []

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, v: str) -> str:
        if v not in ("server", "client"):
            raise ValueError("mode must be 'server' or 'client'")
        return v

    @field_validator("listen_port")
    @classmethod
    def valid_listen_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("listen_port must be between 1 and 65535")
        return v

    @field_validator("private_key")
    @classmethod
    def valid_private_key(cls, v: str) -> str:
        if not v or v == WG_MASKED_SENTINEL:
            return v
        return _valid_wg_key(v, "private_key")

    @field_validator("address")
    @classmethod
    def valid_address(cls, v: str) -> str:
        if not v:
            return v
        try:
            ipaddress.ip_interface(v)
        except ValueError:
            raise ValueError(f"address must be a valid IP/CIDR (e.g. 10.100.0.1/24): {v}")
        return v


class WireguardPeerCreateRequest(BaseModel):
    """
    POST /api/wireguard/peers body. If public_key is omitted, the router
    generates a fresh keypair for this peer: the private key is returned
    exactly once in the response (for the admin to hand to the client, via
    the exported .conf/QR) and is never persisted — the router only ever
    stores the peer's public key, same as it would for a peer whose
    keypair the admin generated themselves elsewhere.
    """
    name: str = ""
    public_key: Optional[str] = None
    allowed_ips: list[str] = []
    endpoint: Optional[str] = None
    persistent_keepalive: Optional[int] = None
    client_address: Optional[str] = None   # this peer's own tunnel address; required when generating a keypair

    @field_validator("name")
    @classmethod
    def valid_name(cls, v: str) -> str:
        if len(v) > 64:
            raise ValueError("name must be 64 characters or fewer")
        if "\n" in v or "\r" in v:
            raise ValueError("name must not contain newlines")
        return v

    @field_validator("public_key")
    @classmethod
    def valid_public_key(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        return _valid_wg_key(v, "public_key")

    @field_validator("allowed_ips")
    @classmethod
    def valid_allowed_ips(cls, v: list[str]) -> list[str]:
        for entry in v:
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError:
                raise ValueError(f"Invalid allowed_ips entry (must be an IP or CIDR): {entry}")
        return v

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if v.count(":") != 1:
            raise ValueError("endpoint must be in host:port form")
        host, _, port_str = v.rpartition(":")
        try:
            port = int(port_str)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise ValueError("endpoint port must be an integer between 1 and 65535")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not _HOSTNAME_RE.match(host):
                raise ValueError(f"endpoint has an invalid host: {host}")
        return v

    @field_validator("persistent_keepalive")
    @classmethod
    def valid_keepalive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not 1 <= v <= 65535:
            raise ValueError("persistent_keepalive must be between 1 and 65535 seconds")
        return v

    @field_validator("client_address")
    @classmethod
    def valid_client_address(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        try:
            ipaddress.ip_interface(v)
        except ValueError:
            raise ValueError(f"client_address must be a valid IP/CIDR: {v}")
        return v

    @model_validator(mode="after")
    def valid_client_address_when_generating(self) -> "WireguardPeerCreateRequest":
        if self.public_key is None and not self.client_address:
            raise ValueError("client_address is required when generating a keypair for this peer")
        return self


# Nebula is scoped "join-only" (#91): this device is never a lighthouse or a
# CA/signing authority — it only imports a host cert/key + CA cert generated
# off-device (via `nebula-cert`) and joins an existing mesh. There is
# deliberately no "am_lighthouse" toggle or CA-signing endpoint here.
NEBULA_MASKED_SENTINEL = "********"

# Loose PEM-shape check only — real cryptographic validation (does the key
# match the cert, is the cert actually signed by this CA) happens via
# `nebula-cert verify` / `nebula -test` in routers/nebula.py, which is the
# only place actual key material gets exercised.
_NEBULA_PEM_RE = re.compile(r'^-----BEGIN [A-Z0-9 ]+-----.*-----END [A-Z0-9 ]+-----\s*$', re.DOTALL)


def _valid_nebula_pem(v: str, field_name: str) -> str:
    if not _NEBULA_PEM_RE.match(v.strip()):
        raise ValueError(f"{field_name} must be PEM-formatted (-----BEGIN ...----- / -----END ...-----)")
    return v


class NebulaFirewallRule(BaseModel):
    """One entry of Nebula's own internal (overlay-only) firewall — separate
    from and in addition to the WAN-facing iptables rules generators/iptables.py
    manages."""
    port: str = "any"    # "any", a single port, or a range like "1000-2000"
    proto: str = "any"   # "any" | "tcp" | "udp" | "icmp"
    host: str = "any"    # "any" or a single nebula overlay IP

    @field_validator("port")
    @classmethod
    def valid_port(cls, v: str) -> str:
        if v == "any":
            return v
        parts = v.split("-")
        if len(parts) > 2:
            raise ValueError("port must be 'any', a number, or a range like '1000-2000'")
        try:
            for p in parts:
                if not 1 <= int(p) <= 65535:
                    raise ValueError
        except ValueError:
            raise ValueError("port must be 'any', a number, or a range like '1000-2000'")
        return v

    @field_validator("proto")
    @classmethod
    def valid_proto(cls, v: str) -> str:
        if v not in ("any", "tcp", "udp", "icmp"):
            raise ValueError("proto must be 'any', 'tcp', 'udp', or 'icmp'")
        return v

    @field_validator("host")
    @classmethod
    def valid_host(cls, v: str) -> str:
        if v == "any":
            return v
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"host must be 'any' or a single nebula overlay IP: {v}")
        return v


class NebulaConfig(BaseModel):
    enabled: bool = False
    listen_port: int = 4242
    lighthouse_hosts: list[str] = []               # this host's lighthouse(s), by overlay IP
    static_host_map: dict[str, list[str]] = {}      # lighthouse overlay IP -> ["public.host:4242", ...]
    cert_pem: str = ""                              # this host's signed cert — public, not sensitive
    key_pem: str = ""                               # write-only (see NEBULA_MASKED_SENTINEL)
    ca_pem: str = ""                                # mesh CA cert — public, not sensitive
    firewall_inbound: list[NebulaFirewallRule] = []
    firewall_outbound: list[NebulaFirewallRule] = [NebulaFirewallRule(port="any", proto="any", host="any")]

    @field_validator("listen_port")
    @classmethod
    def valid_listen_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("listen_port must be between 1 and 65535")
        return v

    @field_validator("lighthouse_hosts")
    @classmethod
    def valid_lighthouse_hosts(cls, v: list[str]) -> list[str]:
        for entry in v:
            try:
                ipaddress.ip_address(entry)
            except ValueError:
                raise ValueError(f"lighthouse_hosts entries must be bare overlay IPs: {entry}")
        return v

    @field_validator("static_host_map")
    @classmethod
    def valid_static_host_map(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for nebula_ip, endpoints in v.items():
            try:
                ipaddress.ip_address(nebula_ip)
            except ValueError:
                raise ValueError(f"static_host_map key must be a nebula overlay IP: {nebula_ip}")
            for ep in endpoints:
                if ep.count(":") != 1:
                    raise ValueError(f"static_host_map endpoint must be host:port: {ep}")
                host, _, port_str = ep.rpartition(":")
                try:
                    if not 1 <= int(port_str) <= 65535:
                        raise ValueError
                except ValueError:
                    raise ValueError(f"static_host_map endpoint port must be 1-65535: {ep}")
        return v

    @field_validator("cert_pem")
    @classmethod
    def valid_cert_pem(cls, v: str) -> str:
        if not v:
            return v
        return _valid_nebula_pem(v, "cert_pem")

    @field_validator("ca_pem")
    @classmethod
    def valid_ca_pem(cls, v: str) -> str:
        if not v:
            return v
        return _valid_nebula_pem(v, "ca_pem")

    @field_validator("key_pem")
    @classmethod
    def valid_key_pem(cls, v: str) -> str:
        if not v or v == NEBULA_MASKED_SENTINEL:
            return v
        return _valid_nebula_pem(v, "key_pem")


class NebulaCredentialsRequest(BaseModel):
    """POST /api/nebula/credentials body — cert/key/CA are validated and
    stored together, since a host cert is only meaningful alongside the CA
    that signed it and the private key it pairs with."""
    cert_pem: str
    key_pem: str
    ca_pem: str

    @field_validator("cert_pem")
    @classmethod
    def valid_cert_pem(cls, v: str) -> str:
        return _valid_nebula_pem(v, "cert_pem")

    @field_validator("key_pem")
    @classmethod
    def valid_key_pem(cls, v: str) -> str:
        return _valid_nebula_pem(v, "key_pem")

    @field_validator("ca_pem")
    @classmethod
    def valid_ca_pem(cls, v: str) -> str:
        return _valid_nebula_pem(v, "ca_pem")
