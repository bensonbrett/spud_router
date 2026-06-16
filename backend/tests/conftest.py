"""
Shared pytest fixtures for spud-router tests.
"""
import pytest


@pytest.fixture
def minimal_state():
    """Bare-minimum valid state — no VLANs, DHCP WAN."""
    return {
        "router": {
            "wan_interface": "eth1",
            "wan_mode": "dhcp",
            "wan_dns": "1.1.1.1",
            "hostname": "spud-router",
            "mgmt_enabled": False,
            "mgmt_interface": "eth0",
            "mgmt_ip": "192.168.1.1",
            "mgmt_prefix": 24,
            "mgmt_dhcp_start": "192.168.1.100",
            "mgmt_dhcp_end": "192.168.1.150",
            "mgmt_dhcp_lease": "12h",
        },
        "vlans": [],
        "static_routes": [],
        "dns_entries": [],
        "fw_inbound": [],
        "fw_intervlan": [],
        "tailscale": {
            "enabled": False,
            "advertise_routes": [],
            "exit_node": False,
            "accept_routes": True,
        },
    }


@pytest.fixture
def vlan_10():
    return {
        "vlan_id": 10,
        "name": "Trusted",
        "interface": "eth0",
        "ip_address": "192.168.10.1",
        "prefix_len": 24,
        "dhcp_enabled": True,
        "dhcp_start": "192.168.10.100",
        "dhcp_end": "192.168.10.200",
        "dhcp_lease": "12h",
        "isolate": False,
    }


@pytest.fixture
def vlan_20():
    return {
        "vlan_id": 20,
        "name": "IoT",
        "interface": "eth0",
        "ip_address": "192.168.20.1",
        "prefix_len": 24,
        "dhcp_enabled": True,
        "dhcp_start": "192.168.20.100",
        "dhcp_end": "192.168.20.200",
        "dhcp_lease": "6h",
        "isolate": True,
    }


@pytest.fixture
def full_state(minimal_state, vlan_10, vlan_20):
    """State with two VLANs, DNS entries, and a static route."""
    state = dict(minimal_state)
    state["vlans"] = [vlan_10, vlan_20]
    state["dns_entries"] = [
        {"hostname": "nas", "ip": "192.168.10.10", "description": "TrueNAS"},
        {"hostname": "proxmox", "ip": "192.168.10.11", "description": ""},
    ]
    state["static_routes"] = [
        {
            "destination": "10.0.0.0/8",
            "gateway": "192.168.10.254",
            "interface": "eth0.10",
            "description": "Corp VPN",
        }
    ]
    return state
