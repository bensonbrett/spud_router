"""
Pydantic models for request/response validation.
All models used across the application live here to avoid circular imports.
"""
from typing import Optional
from pydantic import BaseModel, field_validator
import ipaddress
import re


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


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
        if v not in ("auto", "manual"):
            raise ValueError("wan_dns_mode must be 'auto' or 'manual'")
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
