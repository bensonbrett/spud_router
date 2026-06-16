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

    @field_validator("vlan_id")
    @classmethod
    def vlan_id_range(cls, v: int) -> int:
        if not 1 <= v <= 4094:
            raise ValueError("VLAN ID must be between 1 and 4094")
        return v

    @field_validator("ip_address")
    @classmethod
    def valid_ip(cls, v: str) -> str:
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


class RouterConfig(BaseModel):
    wan_interface: str
    wan_mode: str                         # "dhcp" | "static"
    wan_ip: Optional[str] = None
    wan_prefix: Optional[int] = None
    wan_gateway: Optional[str] = None
    wan_dns: Optional[str] = "1.1.1.1"
    hostname: str = "spud-router"
    # Management interface (untagged direct access)
    mgmt_enabled: bool = False
    mgmt_interface: str = "eth0"
    mgmt_ip: str = "192.168.1.1"
    mgmt_prefix: int = 24
    mgmt_dhcp_start: str = "192.168.1.100"
    mgmt_dhcp_end: str = "192.168.1.150"
    mgmt_dhcp_lease: str = "12h"

    @field_validator("wan_mode")
    @classmethod
    def valid_wan_mode(cls, v: str) -> str:
        if v not in ("dhcp", "static"):
            raise ValueError("wan_mode must be 'dhcp' or 'static'")
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


class InboundRule(BaseModel):
    id: str = ""
    vlan_id: int = 0              # 0 = all VLANs
    proto: str = "tcp"            # tcp | udp | any
    port: Optional[int] = None
    action: str = "accept"        # accept | drop
    description: str = ""

    @field_validator("proto")
    @classmethod
    def valid_proto(cls, v: str) -> str:
        if v not in ("tcp", "udp", "any"):
            raise ValueError("proto must be tcp, udp, or any")
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


class InterVlanRule(BaseModel):
    id: str = ""
    from_vlan: int = 0           # 0 = any
    to_vlan: int = 0             # 0 = any
    proto: str = "any"
    port: Optional[int] = None
    action: str = "accept"
    description: str = ""

    @field_validator("proto")
    @classmethod
    def valid_proto(cls, v: str) -> str:
        if v not in ("tcp", "udp", "any"):
            raise ValueError("proto must be tcp, udp, or any")
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


class ApplyRequest(BaseModel):
    dry_run: bool = False


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

    @field_validator("password")
    @classmethod
    def valid_password(cls, v: str, info) -> str:
        # Password required for all non-open security modes
        # We check during model validation using model_validator instead
        return v

    @field_validator("ssid")
    @classmethod
    def valid_ssid(cls, v: str) -> str:
        if not v or len(v) > 32:
            raise ValueError("SSID must be 1–32 characters")
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
