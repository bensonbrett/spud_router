"""
Tests for POST /api/diagnostics/run — ping/traceroute/nslookup from the router.

subprocess.run is mocked throughout: these tests must never touch the
network, and the whole point of the endpoint is that user input can never
reach a shell, so we assert on the exact argument list passed to
subprocess.run() rather than on real command output.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import backend.state as state_module
import backend.auth as auth_module
from backend.models import DiagnosticRequest


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
