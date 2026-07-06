# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
MCP server — stdio JSON-RPC 2.0 server for AI agents.

Implements the Model Context Protocol over stdin/stdout using JSON-RPC 2.0.
AI agents spawn this as a child process and communicate via tool calls.

Usage:
    python -m backend.mcp

The server reads its API key and configuration from /etc/spud-router/mcp-config.json.
"""
import json
import sys
from typing import Any

from .config import McpConfig
from .http_client import HttpClient
from .tools import McpTools


class McpServer:
    def __init__(self, config: McpConfig):
        self.config = config
        self.client = HttpClient(
            base_url=config.base_url,
            api_key=config.api_key,
            tls_verify=config.tls_verify,
        )
        self.tools = McpTools(self.client, read_only=config.read_only)
        self._request_id = 0

    def _tool_to_response(self, name: str, params: dict) -> dict:
        """Call a tool method and format the response."""
        tool_method = getattr(self.tools, name, None)
        if not tool_method:
            return self._error_response(
                self._request_id,
                -32601,
                f"Method not found: {name}",
            )

        try:
            if params:
                result = tool_method(**params)
            else:
                result = tool_method()
            return self._success_response(self._request_id, result)
        except TypeError as e:
            return self._error_response(
                self._request_id,
                -32602,
                f"Invalid params: {e}",
            )
        except RuntimeError as e:
            return self._error_response(
                self._request_id,
                -32000,
                str(e),
            )
        except Exception as e:
            return self._error_response(
                self._request_id,
                -32001,
                f"Internal error: {e}",
            )

    def _success_response(self, req_id: int | str | None, result: Any) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result,
        }

    def _error_response(self, req_id: int | str | None, code: int, message: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    def _build_tools_list(self) -> list[dict]:
        """Return the list of tools available to this MCP server."""
        read_tools = [
            {"name": "spud_get_state", "description": "Full router state"},
            {"name": "spud_list_interfaces", "description": "Network interfaces"},
            {"name": "spud_list_vlans", "description": "Configured VLANs"},
            {"name": "spud_list_routes", "description": "Static routes"},
            {"name": "spud_list_dns", "description": "DNS entries"},
            {"name": "spud_get_firewall", "description": "All firewall rules"},
            {"name": "spud_get_vpn_status", "description": "VPN status"},
            {"name": "spud_get_system_monitor", "description": "System monitor"},
            {"name": "spud_get_diagnostics", "description": "Interface diagnostics"},
            {"name": "spud_get_config_preview", "description": "Generated config preview"},
            {"name": "spud_get_wireless", "description": "Wireless config"},
            {"name": "spud_get_syslog", "description": "Syslog config"},
            {"name": "spud_get_snmp", "description": "SNMP config"},
        ]

        staging_tools = [
            {"name": "spud_stage_begin", "description": "Begin staging transaction"},
            {"name": "spud_stage_set_router", "description": "Stage router config"},
            {"name": "spud_stage_add_vlan", "description": "Stage VLAN addition"},
            {"name": "spud_stage_update_vlan", "description": "Stage VLAN update"},
            {"name": "spud_stage_delete_vlan", "description": "Stage VLAN deletion"},
            {"name": "spud_stage_add_dns", "description": "Stage DNS entry addition"},
            {"name": "spud_stage_delete_dns", "description": "Stage DNS entry deletion"},
            {"name": "spud_stage_add_route", "description": "Stage route addition"},
            {"name": "spud_stage_delete_route", "description": "Stage route deletion"},
            {"name": "spud_stage_add_fw_rule", "description": "Stage firewall rule addition"},
            {"name": "spud_stage_delete_fw_rule", "description": "Stage firewall rule deletion"},
            {"name": "spud_stage_set_wireless", "description": "Stage wireless config"},
            {"name": "spud_stage_set_vpn", "description": "Stage VPN config"},
            {"name": "spud_stage_validate", "description": "Validate staged changes"},
            {"name": "spud_stage_discard", "description": "Discard staged changes"},
            {"name": "spud_stage_status", "description": "Get staging status"},
            {"name": "spud_stage_commit", "description": "Commit staged changes"},
            {"name": "spud_stage_confirm", "description": "Confirm committed changes"},
        ]

        diagnostic_tools = [
            {"name": "spud_run_diagnostic", "description": "Run ping/traceroute/nslookup"},
            {"name": "spud_wake_on_lan", "description": "Send Wake-on-LAN packet"},
        ]

        vpn_tools = [
            {"name": "spud_set_tailscale", "description": "Configure Tailscale"},
            {"name": "spud_set_tailscale_authkey", "description": "Set Tailscale auth key"},
            {"name": "spud_add_wireguard_peer", "description": "Add WireGuard peer"},
            {"name": "spud_delete_wireguard_peer", "description": "Delete WireGuard peer"},
            {"name": "spud_set_nebula_credentials", "description": "Set Nebula credentials"},
        ]

        if self.config.read_only:
            return read_tools

        return read_tools + staging_tools + diagnostic_tools + vpn_tools

    def handle_message(self, msg: dict) -> dict | None:
        """Handle an incoming JSON-RPC message."""
        method = msg.get("method", "")
        params = msg.get("params", {})
        self._request_id = msg.get("id")

        if method == "initialize":
            return self._success_response(self._request_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "spud-router-mcp",
                    "version": "1.0.0",
                },
            })

        if method == "tools/list":
            return self._success_response(self._request_id, {
                "tools": self._build_tools_list(),
            })

        if method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            return self._tool_to_response(tool_name, tool_args)

        if method == "ping":
            return self._success_response(self._request_id, None)

        return self._error_response(
            self._request_id,
            -32601,
            f"Method not found: {method}",
        )

    def run(self):
        """Read JSON-RPC messages from stdin and write responses to stdout."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                sys.stdout.write(
                    json.dumps(
                        self._error_response(None, -32700, "Parse error")
                    ) + "\n"
                )
                sys.stdout.flush()
                continue

            response = self.handle_message(msg)
            if response:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
