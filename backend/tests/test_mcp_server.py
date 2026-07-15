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
    # Parity-sweep additions: BGP, DHCP reservations, syslog/snmp write, port-forward
    # staging, and SSID staging all need a tool present in the list.
    for name in (
        "spud_get_bgp",
        "spud_set_bgp",
        "spud_list_reservations",
        "spud_add_reservation",
        "spud_delete_reservation",
        "spud_stage_set_syslog",
        "spud_stage_set_snmp",
        "spud_stage_add_port_forward",
        "spud_stage_update_port_forward",
        "spud_stage_delete_port_forward",
        "spud_stage_add_ssid",
        "spud_stage_delete_ssid",
    ):
        assert name in tools, f"{name} missing from tools/list"


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


def test_get_bgp_reads_config_and_status():
    client = RecordingClient()
    tools = McpTools(client)

    result = tools.spud_get_bgp()

    assert client.paths == ["/api/bgp", "/api/bgp/status"]
    assert result == {
        "config": {"path": "/api/bgp"},
        "status": {"path": "/api/bgp/status"},
    }


def test_list_reservations_targets_vlan_path():
    client = RecordingClient()
    tools = McpTools(client)

    tools.spud_list_reservations(10)

    assert client.paths == ["/api/vlans/10/reservations"]


class RecordingWriteClient(RecordingClient):
    def __init__(self):
        super().__init__()
        self.posted: list[tuple[str, dict | None]] = []
        self.puts: list[tuple[str, dict | None]] = []
        self.deleted: list[str] = []

    def post(self, path: str, body: dict | None = None):
        self.posted.append((path, body))
        return {"ok": True}

    def put(self, path: str, body: dict | None = None):
        self.puts.append((path, body))
        return {"ok": True}

    def delete(self, path: str):
        self.deleted.append(path)
        return {"ok": True}


def test_stage_port_forward_tools_use_expected_ops():
    client = RecordingWriteClient()
    tools = McpTools(client)

    tools.spud_stage_add_port_forward({"proto": "tcp"})
    tools.spud_stage_update_port_forward({"id": "abc"})
    tools.spud_stage_delete_port_forward("abc")

    assert client.posted == [
        ("/api/staging/op", {"op": "add_port_forward", "data": {"proto": "tcp"}}),
        ("/api/staging/op", {"op": "update_port_forward", "data": {"id": "abc"}}),
        ("/api/staging/op", {"op": "delete_port_forward", "data": {"forward_id": "abc"}}),
    ]


def test_stage_syslog_and_snmp_tools_use_expected_ops():
    client = RecordingWriteClient()
    tools = McpTools(client)

    tools.spud_stage_set_syslog({"enabled": True})
    tools.spud_stage_set_snmp({"enabled": True})

    assert client.posted == [
        ("/api/staging/op", {"op": "set_syslog", "data": {"enabled": True}}),
        ("/api/staging/op", {"op": "set_snmp", "data": {"enabled": True}}),
    ]


def test_stage_ssid_tools_use_expected_ops():
    client = RecordingWriteClient()
    tools = McpTools(client)

    tools.spud_stage_add_ssid({"ssid": "guest"})
    tools.spud_stage_delete_ssid("ssid1")

    assert client.posted == [
        ("/api/staging/op", {"op": "add_ssid", "data": {"ssid": "guest"}}),
        ("/api/staging/op", {"op": "delete_ssid", "data": {"ssid_id": "ssid1"}}),
    ]


def test_set_bgp_is_a_direct_put_not_staged():
    client = RecordingWriteClient()
    tools = McpTools(client)

    tools.spud_set_bgp({"asn": 65010})

    assert client.puts == [("/api/bgp", {"asn": 65010})]
    assert client.posted == []


def test_reservation_write_tools_target_vlan_scoped_paths():
    client = RecordingWriteClient()
    tools = McpTools(client)

    tools.spud_add_reservation(10, {"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.5"})
    tools.spud_delete_reservation(10, "res1")

    assert client.posted == [
        ("/api/vlans/10/reservations", {"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.5"}),
    ]
    assert client.deleted == ["/api/vlans/10/reservations/res1"]


def test_write_tools_blocked_in_read_only_mode():
    client = RecordingWriteClient()
    tools = McpTools(client, read_only=True)

    for fn in (
        lambda: tools.spud_stage_set_syslog({}),
        lambda: tools.spud_stage_set_snmp({}),
        lambda: tools.spud_stage_add_ssid({}),
        lambda: tools.spud_set_bgp({}),
        lambda: tools.spud_add_reservation(1, {}),
        lambda: tools.spud_update_wireguard_peer("p1", {}),
    ):
        try:
            fn()
            assert False, "expected RuntimeError in read-only mode"
        except RuntimeError:
            pass


def test_update_wireguard_peer_is_a_direct_put_not_staged():
    client = RecordingWriteClient()
    tools = McpTools(client)

    tools.spud_update_wireguard_peer("p1", {"name": "renamed"})

    assert client.puts == [("/api/wireguard/peers/p1", {"name": "renamed"})]
    assert client.posted == []


def test_update_wireguard_peer_tool_present_in_tools_list():
    server = _server()

    response = server.handle_message({
        "jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {},
    })

    tools = {tool["name"]: tool for tool in response["result"]["tools"]}
    assert "spud_update_wireguard_peer" in tools
    assert tools["spud_update_wireguard_peer"]["inputSchema"]["required"] == ["peer_id", "data"]
