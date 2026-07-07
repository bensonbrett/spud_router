# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)

from backend.mcp.config import McpConfig
from backend.mcp.server import McpServer
from backend.mcp.tools import McpTools


def _server() -> McpServer:
    return McpServer(McpConfig(api_key="spud_test", base_url="https://router.local:8080"))


class DummyTools:
    def spud_list_vlans(self):
        return [{"vlan_id": 10, "name": "LAN"}]

    def spud_fail(self):
        raise RuntimeError("backend unavailable")


class RecordingClient:
    def __init__(self):
        self.paths: list[str] = []

    def get(self, path: str):
        self.paths.append(path)
        return {"path": path}


def test_initialize_negotiates_requested_supported_protocol():
    server = _server()

    response = server.handle_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    })

    assert response["result"]["protocolVersion"] == "2025-06-18"
    assert response["result"]["capabilities"]["tools"] == {"listChanged": False}


def test_initialized_notification_is_ignored():
    server = _server()

    response = server.handle_message({
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    })

    assert response is None


def test_tools_list_includes_required_argument_schema():
    server = _server()

    response = server.handle_message({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    })

    tools = {tool["name"]: tool for tool in response["result"]["tools"]}
    assert tools["spud_list_vlans"]["inputSchema"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert tools["spud_stage_delete_vlan"]["inputSchema"]["required"] == ["vlan_id"]
    assert tools["spud_run_diagnostic"]["inputSchema"]["required"] == ["command", "target"]


def test_tools_call_returns_mcp_tool_result_shape():
    server = _server()
    server.tools = DummyTools()

    response = server.handle_message({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "spud_list_vlans", "arguments": {}},
    })

    result = response["result"]
    assert result["isError"] is False
    assert result["structuredContent"] == {"result": [{"vlan_id": 10, "name": "LAN"}]}
    assert result["content"][0]["type"] == "text"
    assert '"vlan_id": 10' in result["content"][0]["text"]


def test_tool_runtime_error_returns_mcp_tool_error_result():
    server = _server()
    server.tools = DummyTools()

    response = server.handle_message({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "spud_fail", "arguments": {}},
    })

    assert "error" not in response
    result = response["result"]
    assert result["isError"] is True
    assert result["structuredContent"] == {"error": "backend unavailable"}
    assert result["content"][0]["text"] == "backend unavailable"


def test_unknown_tool_remains_protocol_error():
    server = _server()

    response = server.handle_message({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "missing_tool", "arguments": {}},
    })

    assert response["error"]["code"] == -32601


def test_firewall_tool_uses_existing_backend_routes():
    client = RecordingClient()
    tools = McpTools(client)

    result = tools.spud_get_firewall()

    assert client.paths == [
        "/api/firewall/inbound",
        "/api/firewall/intervlan",
        "/api/firewall/outbound",
        "/api/firewall/port-forward",
    ]
    assert result["fw_intervlan"] == {"path": "/api/firewall/intervlan"}
    assert result["port_forwards"] == {"path": "/api/firewall/port-forward"}
