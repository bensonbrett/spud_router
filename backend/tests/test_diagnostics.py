# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Tests for POST /api/diagnostics/run — ping/traceroute/nslookup from the router.

subprocess.run is mocked throughout: these tests must never touch the
network, and the whole point of the endpoint is that user input can never
reach a shell, so we assert on the exact argument list passed to
subprocess.run() rather than on real command output.
"""
import socket
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import backend.state as state_module
import backend.auth as auth_module
from backend.models import DiagnosticRequest, WolRequest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    conf_dir   = tmp_path / "spud-router"
    state_file = conf_dir / "state.json"
    auth_file  = conf_dir / "auth.json"
    monkeypatch.setattr(state_module, "SPUD_CONF",         conf_dir)
    monkeypatch.setattr(state_module, "STATE_FILE",        state_file)
    monkeypatch.setattr(auth_module,  "AUTH_FILE",         auth_file)
    monkeypatch.setattr(auth_module,  "SPUD_CONF",         conf_dir)
    monkeypatch.setattr(auth_module,  "CLI_TOKEN_FILE",    conf_dir / "cli-token")
    monkeypatch.setattr(auth_module,  "TOKEN_SECRET_FILE", conf_dir / "token-secret")
    monkeypatch.setattr(auth_module,  "_revoked",          set())


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def authed_client(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "spudrouter"})
    assert resp.status_code == 200
    token = resp.json()["token"]
    client.headers.update({"X-Session-Token": token})
    return client


class TestDiagnosticRequestModel:
    def test_valid_ip_target(self):
        r = DiagnosticRequest(command="ping", target="8.8.8.8")
        assert r.target == "8.8.8.8"

    def test_valid_hostname_target(self):
        r = DiagnosticRequest(command="nslookup", target="example.com")
        assert r.target == "example.com"

    def test_invalid_command_rejected(self):
        with pytest.raises(ValidationError, match="ping, traceroute, or nslookup"):
            DiagnosticRequest(command="rm", target="8.8.8.8")

    @pytest.mark.parametrize("target", [
        "8.8.8.8; rm -rf /",
        "$(whoami)",
        "a b",
        "`id`",
        "8.8.8.8|cat /etc/passwd",
        "../../etc/passwd",
        "host&&ls",
    ])
    def test_injection_shaped_targets_rejected(self, target):
        with pytest.raises(ValidationError):
            DiagnosticRequest(command="ping", target=target)

    def test_empty_target_rejected(self):
        with pytest.raises(ValidationError):
            DiagnosticRequest(command="ping", target="")


class TestDiagnosticsRunEndpoint:
    def test_requires_auth(self, client):
        resp = client.post("/api/diagnostics/run", json={"command": "ping", "target": "8.8.8.8"})
        assert resp.status_code == 401

    def test_invalid_command_returns_422(self, authed_client):
        resp = authed_client.post("/api/diagnostics/run", json={"command": "rm", "target": "8.8.8.8"})
        assert resp.status_code == 422

    def test_injection_target_returns_422(self, authed_client):
        resp = authed_client.post("/api/diagnostics/run", json={
            "command": "ping", "target": "8.8.8.8; rm -rf /",
        })
        assert resp.status_code == 422

    def test_ping_arg_list_is_exact(self, authed_client):
        with patch("backend.routers.diagnostics.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "4 packets transmitted"
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0
            resp = authed_client.post("/api/diagnostics/run", json={"command": "ping", "target": "8.8.8.8"})
        assert resp.status_code == 200
        args, kwargs = mock_run.call_args
        assert args[0] == ["ping", "-c", "4", "-w", "10", "8.8.8.8"]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert "shell" not in kwargs or kwargs["shell"] is False

    def test_nslookup_arg_list_is_exact(self, authed_client):
        with patch("backend.routers.diagnostics.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "Name: example.com"
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0
            resp = authed_client.post("/api/diagnostics/run", json={"command": "nslookup", "target": "example.com"})
        assert resp.status_code == 200
        assert mock_run.call_args[0][0] == ["nslookup", "example.com"]

    def test_traceroute_arg_list_when_available(self, authed_client):
        with patch("backend.routers.diagnostics.shutil.which", return_value="/usr/bin/traceroute"), \
             patch("backend.routers.diagnostics.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "traceroute output"
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0
            resp = authed_client.post("/api/diagnostics/run", json={"command": "traceroute", "target": "8.8.8.8"})
        assert resp.status_code == 200
        assert mock_run.call_args[0][0] == ["traceroute", "-w", "2", "-q", "1", "-m", "20", "8.8.8.8"]

    def test_traceroute_falls_back_to_tracepath(self, authed_client):
        with patch("backend.routers.diagnostics.shutil.which", return_value=None), \
             patch("backend.routers.diagnostics.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "tracepath output"
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0
            resp = authed_client.post("/api/diagnostics/run", json={"command": "traceroute", "target": "8.8.8.8"})
        assert resp.status_code == 200
        assert mock_run.call_args[0][0] == ["tracepath", "8.8.8.8"]

    def test_response_shape(self, authed_client):
        with patch("backend.routers.diagnostics.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "output here"
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0
            resp = authed_client.post("/api/diagnostics/run", json={"command": "ping", "target": "8.8.8.8"})
        body = resp.json()
        assert body["command"] == "ping"
        assert body["target"] == "8.8.8.8"
        assert body["exit_code"] == 0
        assert body["output"] == "output here"
        assert body["truncated"] is False
        assert body["timed_out"] is False

    def test_output_truncated_past_16kb(self, authed_client):
        with patch("backend.routers.diagnostics.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "x" * 20000
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0
            resp = authed_client.post("/api/diagnostics/run", json={"command": "ping", "target": "8.8.8.8"})
        body = resp.json()
        assert body["truncated"] is True
        assert len(body["output"]) == 16 * 1024

    def test_timeout_returns_partial_and_flag(self, authed_client):
        import subprocess as sp
        with patch("backend.routers.diagnostics.subprocess.run") as mock_run:
            mock_run.side_effect = sp.TimeoutExpired(cmd=["ping"], timeout=15, output="partial", stderr="")
            resp = authed_client.post("/api/diagnostics/run", json={"command": "ping", "target": "8.8.8.8"})
        body = resp.json()
        assert body["timed_out"] is True
        assert "partial" in body["output"]
        assert body["exit_code"] is None


class TestWolRequestModel:
    def test_valid_mac_colon_separated(self):
        r = WolRequest(mac="AA:BB:CC:DD:EE:FF")
        assert r.mac == "aa:bb:cc:dd:ee:ff"

    def test_valid_mac_hyphen_separated_normalized(self):
        r = WolRequest(mac="aa-bb-cc-dd-ee-ff")
        assert r.mac == "aa:bb:cc:dd:ee:ff"

    def test_mixed_case_normalized_to_lowercase(self):
        r = WolRequest(mac="Aa:Bb:Cc:Dd:Ee:Ff")
        assert r.mac == "aa:bb:cc:dd:ee:ff"

    @pytest.mark.parametrize("mac", [
        "aa:bb:cc:dd:ee",             # too short
        "aa:bb:cc:dd:ee:ff:gg",       # too long
        "gg:bb:cc:dd:ee:ff",          # invalid hex
        "aa:bb:cc:dd:ee:ff; rm -rf /",
        "$(whoami)",
        "aa bb cc dd ee ff",
        "`id`",
        "aabbccddeeff",               # no separators at all
        "aa:bb:cc:dd:ee:ff|cat /etc/passwd",
        "",
    ])
    def test_malformed_or_injection_shaped_mac_rejected(self, mac):
        with pytest.raises(ValidationError):
            WolRequest(mac=mac)

    def test_vlan_id_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            WolRequest(mac="aa:bb:cc:dd:ee:ff", vlan_id=5000)

    def test_invalid_broadcast_rejected(self):
        with pytest.raises(ValidationError):
            WolRequest(mac="aa:bb:cc:dd:ee:ff", broadcast="not-an-ip")

    def test_vlan_id_and_broadcast_mutually_exclusive(self):
        with pytest.raises(ValidationError):
            WolRequest(mac="aa:bb:cc:dd:ee:ff", vlan_id=10, broadcast="192.168.10.255")

    def test_defaults_have_no_vlan_or_broadcast(self):
        r = WolRequest(mac="aa:bb:cc:dd:ee:ff")
        assert r.vlan_id is None
        assert r.broadcast is None


class _FakeSocketModule:
    """
    Stand-in for the stdlib `socket` module, bound only onto the name
    `backend.routers.diagnostics.socket` for the duration of a test.
    Patching attributes of the *real* socket module (e.g.
    `patch("backend.routers.diagnostics.socket.socket")`) mutates the
    actual global `socket.socket` class for the whole process — including
    the event loop FastAPI's TestClient spins up per request — and
    reliably breaks it (see test_syslog_api.py's identical pattern).
    Rebinding the module-level name instead leaves the real module
    untouched.
    """
    def __init__(self):
        self.AF_INET     = socket.AF_INET
        self.SOCK_DGRAM   = socket.SOCK_DGRAM
        self.SOL_SOCKET   = socket.SOL_SOCKET
        self.SO_BROADCAST = socket.SO_BROADCAST
        self.socket = MagicMock()


def _fake_socket_with_mock(monkeypatch):
    import backend.routers.diagnostics as diagnostics_module
    fake = _FakeSocketModule()
    mock_sock = MagicMock()
    fake.socket.return_value.__enter__.return_value = mock_sock
    monkeypatch.setattr(diagnostics_module, "socket", fake)
    return mock_sock


class TestWolEndpoint:
    def test_requires_auth(self, client):
        resp = client.post("/api/diagnostics/wol", json={"mac": "aa:bb:cc:dd:ee:ff"})
        assert resp.status_code == 401

    def test_invalid_mac_returns_422(self, authed_client):
        resp = authed_client.post("/api/diagnostics/wol", json={"mac": "not-a-mac"})
        assert resp.status_code == 422

    def test_default_broadcast_sends_exact_magic_packet(self, authed_client, monkeypatch):
        mock_sock = _fake_socket_with_mock(monkeypatch)
        resp = authed_client.post("/api/diagnostics/wol", json={"mac": "AA:BB:CC:DD:EE:FF"})

        assert resp.status_code == 200
        body = resp.json()
        assert body == {"sent": True, "mac": "aa:bb:cc:dd:ee:ff", "broadcast": "255.255.255.255"}

        mock_sock.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        mac_bytes = bytes.fromhex("aabbccddeeff")
        expected_payload = b"\xff" * 6 + mac_bytes * 16
        mock_sock.sendto.assert_called_once_with(expected_payload, ("255.255.255.255", 9))

    def test_explicit_broadcast_override_used(self, authed_client, monkeypatch):
        mock_sock = _fake_socket_with_mock(monkeypatch)
        resp = authed_client.post("/api/diagnostics/wol", json={
            "mac": "aa:bb:cc:dd:ee:ff", "broadcast": "10.0.0.255",
        })
        assert resp.status_code == 200
        assert resp.json()["broadcast"] == "10.0.0.255"
        mock_sock.sendto.assert_called_once()
        assert mock_sock.sendto.call_args[0][1] == ("10.0.0.255", 9)

    def test_vlan_id_resolves_vlan_broadcast_address(self, authed_client, monkeypatch):
        state = state_module.load_state()
        state["vlans"] = [{
            "vlan_id": 10, "name": "Trusted", "interface": "eth0",
            "ip_address": "192.168.10.1", "prefix_len": 24,
        }]
        state_module.save_state(state)

        mock_sock = _fake_socket_with_mock(monkeypatch)
        resp = authed_client.post("/api/diagnostics/wol", json={
            "mac": "aa:bb:cc:dd:ee:ff", "vlan_id": 10,
        })
        assert resp.status_code == 200
        assert resp.json()["broadcast"] == "192.168.10.255"
        assert mock_sock.sendto.call_args[0][1] == ("192.168.10.255", 9)

    def test_unknown_vlan_id_returns_400(self, authed_client):
        resp = authed_client.post("/api/diagnostics/wol", json={
            "mac": "aa:bb:cc:dd:ee:ff", "vlan_id": 99,
        })
        assert resp.status_code == 400

    def test_socket_error_returns_clean_error_not_500(self, authed_client, monkeypatch):
        mock_sock = _fake_socket_with_mock(monkeypatch)
        mock_sock.sendto.side_effect = OSError("Network is unreachable")
        resp = authed_client.post("/api/diagnostics/wol", json={"mac": "aa:bb:cc:dd:ee:ff"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["sent"] is False
        assert body["mac"] == "aa:bb:cc:dd:ee:ff"
        assert "Network is unreachable" in body["error"]
