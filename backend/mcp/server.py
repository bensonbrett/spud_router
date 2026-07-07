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

    @staticmethod
    def _tool(name: str, description: str) -> dict:
        """Build a tool entry with the required inputSchema."""
        return {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        }

    def _build_tools_list(self) -> list[dict]:
        """Return the list of tools available to this MCP server."""
        read_tools = [
            self._tool("spud_get_state", "Full router state"),
            self._tool("spud_list_interfaces", "Network interfaces"),
            self._tool("spud_list_vlans", "Configured VLANs"),
            self._tool("spud_list_routes", "Static routes"),
            self._tool("spud_list_dns", "DNS entries"),
            self._tool("spud_get_firewall", "All firewall rules"),
            self._tool("spud_get_vpn_status", "VPN status"),
            self._tool("spud_get_system_monitor", "System monitor"),
            self._tool("spud_get_diagnostics", "Interface diagnostics"),
            self._tool("spud_get_config_preview", "Generated config preview"),
            self._tool("spud_get_wireless", "Wireless config"),
            self._tool("spud_get_syslog", "Syslog config"),
            self._tool("spud_get_snmp", "SNMP config"),
        ]

        staging_tools = [
            self._tool("spud_stage_begin", "Begin staging transaction"),
            self._tool("spud_stage_set_router", "Stage router config"),
            self._tool("spud_stage_add_vlan", "Stage VLAN addition"),
            self._tool("spud_stage_update_vlan", "Stage VLAN update"),
            self._tool("spud_stage_delete_vlan", "Stage VLAN deletion"),
            self._tool("spud_stage_add_dns", "Stage DNS entry addition"),
            self._tool("spud_stage_delete_dns", "Stage DNS entry deletion"),
            self._tool("spud_stage_add_route", "Stage route addition"),
            self._tool("spud_stage_delete_route", "Stage route deletion"),
            self._tool("spud_stage_add_fw_rule", "Stage firewall rule addition"),
            self._tool("spud_stage_delete_fw_rule", "Stage firewall rule deletion"),
            self._tool("spud_stage_set_wireless", "Stage wireless config"),
            self._tool("spud_stage_set_vpn", "Stage VPN config"),
            self._tool("spud_stage_validate", "Validate staged changes"),
            self._tool("spud_stage_discard", "Discard staged changes"),
            self._tool("spud_stage_status", "Get staging status"),
            self._tool("spud_stage_commit", "Commit staged changes"),
            self._tool("spud_stage_confirm", "Confirm committed changes"),
        ]

        diagnostic_tools = [
            self._tool("spud_run_diagnostic", "Run ping/traceroute/nslookup"),
            self._tool("spud_wake_on_lan", "Send Wake-on-LAN packet"),
        ]

        vpn_tools = [
            self._tool("spud_set_tailscale", "Configure Tailscale"),
            self._tool("spud_set_tailscale_authkey", "Set Tailscale auth key"),
            self._tool("spud_add_wireguard_peer", "Add WireGuard peer"),
            self._tool("spud_delete_wireguard_peer", "Delete WireGuard peer"),
            self._tool("spud_set_nebula_credentials", "Set Nebula credentials"),
        ]

        if self.config.read_only:
            return read_tools

        return read_tools + staging_tools + diagnostic_tools + vpn_tools

    def handle_message(self, msg: dict) -> dict | None:
        """Handle an incoming JSON-RPC message."""
        method = msg.get("method", "")
        params = msg.get("params", {})
        self._request_id = msg.get("id")

        # JSON-RPC notifications have no id — silently ignore them
        if self._request_id is None:
            return None

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
