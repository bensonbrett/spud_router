# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
MCP tool handlers.

Each tool maps to one or more HTTP calls against the spud-router backend API.
Tools are organized by scope: read, staging, apply, diagnostics, vpn.
"""
from .http_client import HttpClient


class McpTools:
    def __init__(self, client: HttpClient, read_only: bool = False):
        self.client = client
        self.read_only = read_only

    # ── Read tools ────────────────────────────────────────────────────────────────

    def spud_get_state(self) -> dict:
        """Full router state (vlans, router, firewall, vpn)."""
        return self.client.get("/api/state")

    def spud_list_interfaces(self) -> list:
        """Physical network interfaces + link state."""
        return self.client.get("/api/interfaces")

    def spud_list_vlans(self) -> list:
        """Configured VLANs with DHCP info."""
        return self.client.get("/api/vlans")

    def spud_list_routes(self) -> list:
        """Static routes."""
        return self.client.get("/api/routes")

    def spud_list_dns(self) -> list:
        """DNS entries."""
        return self.client.get("/api/dns")

    def spud_get_firewall(self) -> dict:
        """All firewall rules (inbound, inter-VLAN, outbound, port forwards)."""
        return {
            "fw_inbound": self.client.get("/api/firewall/inbound"),
            "fw_intervlan": self.client.get("/api/firewall/intervlan"),
            "fw_outbound": self.client.get("/api/firewall/outbound"),
            "port_forwards": self.client.get("/api/firewall/port-forward"),
        }

    def spud_get_vpn_status(self) -> dict:
        """Tailscale status, WireGuard peers, Nebula info."""
        tailscale = None
        wireguard = None
        nebula = None
        try:
            tailscale = self.client.get("/api/tailscale/status")
        except Exception:
            pass
        try:
            wireguard = self.client.get("/api/wireguard")
        except Exception:
            pass
        try:
            nebula = self.client.get("/api/nebula")
        except Exception:
            pass
        return {
            "tailscale": tailscale,
            "wireguard": wireguard,
            "nebula": nebula,
        }

    def spud_get_system_monitor(self) -> dict:
        """CPU, memory, disk, interface counters."""
        return self.client.get("/api/system/monitor")

    def spud_get_diagnostics(self) -> dict:
        """Interface diagnostics (carrier, addresses, leases)."""
        return self.client.get("/api/diagnostics")

    def spud_get_config_preview(self) -> dict:
        """Generated netplan/dnsmasq/iptables without applying."""
        return self.client.get("/api/preview")

    def spud_get_wireless(self) -> dict:
        """Wireless config and SSIDs."""
        return self.client.get("/api/wireless")

    def spud_get_syslog(self) -> dict:
        """Syslog forwarding config."""
        return self.client.get("/api/syslog")

    def spud_get_snmp(self) -> dict:
        """SNMP config (community strings masked)."""
        return self.client.get("/api/snmp")

    # ── Staging write tools ─────────────────────────────────────────────────────

    def _check_not_read_only(self):
        if self.read_only:
            raise RuntimeError("MCP server is in read-only mode")

    def spud_stage_begin(self) -> dict:
        """Snapshot live state into staging buffer."""
        self._check_not_read_only()
        return self.client.post("/api/staging/begin")

    def spud_stage_set_router(self, data: dict) -> dict:
        """Stage WAN/router config change."""
        self._check_not_read_only()
        return self.client.post("/api/staging/op", {"op": "set_router", "data": data})

    def spud_stage_add_vlan(self, data: dict) -> dict:
        """Stage a VLAN addition."""
        self._check_not_read_only()
        return self.client.post("/api/staging/op", {"op": "add_vlan", "data": data})

    def spud_stage_update_vlan(self, data: dict) -> dict:
        """Stage a VLAN modification."""
        self._check_not_read_only()
        return self.client.post("/api/staging/op", {"op": "update_vlan", "data": data})

    def spud_stage_delete_vlan(self, vlan_id: int) -> dict:
        """Stage a VLAN removal."""
        self._check_not_read_only()
        return self.client.post("/api/staging/op", {"op": "delete_vlan", "data": {"vlan_id": vlan_id}})

    def spud_stage_add_dns(self, data: dict) -> dict:
        """Stage a DNS entry addition."""
        self._check_not_read_only()
        return self.client.post("/api/staging/op", {"op": "add_dns", "data": data})

    def spud_stage_delete_dns(self, hostname: str) -> dict:
        """Stage a DNS entry removal."""
        self._check_not_read_only()
        return self.client.post("/api/staging/op", {"op": "delete_dns", "data": {"hostname": hostname}})

    def spud_stage_add_route(self, data: dict) -> dict:
        """Stage a static route addition."""
        self._check_not_read_only()
        return self.client.post("/api/staging/op", {"op": "add_route", "data": data})

    def spud_stage_delete_route(self, destination: str) -> dict:
        """Stage a static route removal."""
        self._check_not_read_only()
        return self.client.post("/api/staging/op", {"op": "delete_route", "data": {"destination": destination}})

    def spud_stage_add_fw_rule(self, rule_type: str, data: dict) -> dict:
        """Stage a firewall rule addition."""
        self._check_not_read_only()
        op_map = {
            "inbound": "add_fw_inbound",
            "intervlan": "add_fw_intervlan",
            "outbound": "add_fw_outbound",
        }
        op = op_map.get(rule_type)
        if not op:
            raise ValueError(f"Invalid rule_type: {rule_type}")
        return self.client.post("/api/staging/op", {"op": op, "data": data})

    def spud_stage_delete_fw_rule(self, rule_type: str, rule_id: str) -> dict:
        """Stage a firewall rule removal."""
        self._check_not_read_only()
        op_map = {
            "inbound": "delete_fw_inbound",
            "intervlan": "delete_fw_intervlan",
            "outbound": "delete_fw_outbound",
        }
        op = op_map.get(rule_type)
        if not op:
            raise ValueError(f"Invalid rule_type: {rule_type}")
        return self.client.post("/api/staging/op", {"op": op, "data": {"id": rule_id}})

    def spud_stage_set_wireless(self, data: dict) -> dict:
        """Stage wireless config / SSID change."""
        self._check_not_read_only()
        return self.client.post("/api/staging/op", {"op": "set_wireless", "data": data})

    def spud_stage_set_vpn(self, vpn_type: str, data: dict) -> dict:
        """Stage VPN config change (tailscale, wireguard, nebula)."""
        self._check_not_read_only()
        op_map = {
            "tailscale": "set_tailscale",
            "wireguard": "set_wireguard",
            "nebula": "set_nebula",
        }
        op = op_map.get(vpn_type)
        if not op:
            raise ValueError(f"Invalid vpn_type: {vpn_type}")
        return self.client.post("/api/staging/op", {"op": op, "data": data})

    def spud_stage_validate(self) -> dict:
        """Validate the full staged state."""
        self._check_not_read_only()
        return self.client.post("/api/staging/validate")

    def spud_stage_discard(self) -> dict:
        """Abandon the staging buffer."""
        self._check_not_read_only()
        return self.client.post("/api/staging/discard")

    def spud_stage_status(self) -> dict:
        """Inspect staging buffer."""
        return self.client.get("/api/staging/status")

    # ── Staging apply tools ─────────────────────────────────────────────────────

    def spud_stage_commit(self) -> dict:
        """Promote staged state to live + arm auto-revert."""
        self._check_not_read_only()
        return self.client.post("/api/staging/commit")

    def spud_stage_confirm(self, token: str) -> dict:
        """Cancel auto-revert watchdog."""
        self._check_not_read_only()
        return self.client.post("/api/staging/confirm", {"token": token})

    # ── Diagnostic tools ─────────────────────────────────────────────────────────

    def spud_run_diagnostic(self, command: str, target: str) -> dict:
        """Run ping/traceroute/nslookup from the router."""
        return self.client.post("/api/diagnostics/run", {"command": command, "target": target})

    def spud_wake_on_lan(self, mac: str, vlan_id: int | None = None, broadcast: str | None = None) -> dict:
        """Send WOL magic packet."""
        body = {"mac": mac}
        if vlan_id is not None:
            body["vlan_id"] = vlan_id
        if broadcast is not None:
            body["broadcast"] = broadcast
        return self.client.post("/api/diagnostics/wol", body)

    # ── VPN tools ────────────────────────────────────────────────────────────────

    def spud_set_tailscale(self, data: dict) -> dict:
        """Configure Tailscale settings."""
        self._check_not_read_only()
        return self.client.post("/api/tailscale", data)

    def spud_set_tailscale_authkey(self, auth_key: str) -> dict:
        """Set Tailscale auth key."""
        self._check_not_read_only()
        return self.client.post("/api/tailscale/authkey", {"auth_key": auth_key})

    def spud_add_wireguard_peer(self, data: dict) -> dict:
        """Create WireGuard peer."""
        self._check_not_read_only()
        return self.client.post("/api/wireguard/peers", data)

    def spud_delete_wireguard_peer(self, peer_id: str) -> dict:
        """Remove WireGuard peer."""
        self._check_not_read_only()
        return self.client.delete(f"/api/wireguard/peers/{peer_id}")

    def spud_set_nebula_credentials(self, cert_pem: str, key_pem: str, ca_pem: str) -> dict:
        """Import Nebula cert/key/CA."""
        self._check_not_read_only()
        return self.client.post("/api/nebula/credentials", {
            "cert_pem": cert_pem,
            "key_pem": key_pem,
            "ca_pem": ca_pem,
        })
