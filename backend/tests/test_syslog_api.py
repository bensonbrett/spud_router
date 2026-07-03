"""Tests for GET/PUT /api/syslog and POST /api/syslog/test."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import backend.state as state_module
import backend.auth as auth_module


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


class TestGetSyslog:
    def test_default_disabled(self, authed_client):
        resp = authed_client.get("/api/syslog")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_requires_auth(self, client):
        assert client.get("/api/syslog").status_code == 401


class TestPutSyslog:
    def test_round_trip(self, authed_client):
        payload = {
            "enabled": True, "server": "logs.example.com", "port": 6514,
            "protocol": "tcp", "facility": "local0", "severity": "err",
            "keep_local": False,
        }
        resp = authed_client.put("/api/syslog", json=payload)
        assert resp.status_code == 200
        got = authed_client.get("/api/syslog").json()
        assert got["server"] == "logs.example.com"
        assert got["port"] == 6514
        assert got["keep_local"] is False

    def test_enabled_without_server_rejected(self, authed_client):
        resp = authed_client.put("/api/syslog", json={"enabled": True, "server": ""})
        assert resp.status_code == 422

    def test_bad_facility_rejected(self, authed_client):
        resp = authed_client.put("/api/syslog", json={"facility": "; rm -rf /"})
        assert resp.status_code == 422

    def test_bad_protocol_rejected(self, authed_client):
        resp = authed_client.put("/api/syslog", json={"protocol": "ssl"})
        assert resp.status_code == 422


class _FakeSocketModule:
    """
    Stand-in for the stdlib `socket` module, bound only onto the name
    `backend.routers.syslog.socket` for the duration of a test. Patching
    attributes of the *real* socket module (e.g.
    `patch("backend.routers.syslog.socket.socket")`) mutates the actual
    global `socket.socket` class for the whole process — including the
    event loop FastAPI's TestClient spins up per request — and reliably
    breaks it. Rebinding the module-level name instead leaves the real
    module untouched.
    """
    def __init__(self):
        import socket as _real_socket
        self.AF_INET    = _real_socket.AF_INET
        self.SOCK_DGRAM = _real_socket.SOCK_DGRAM
        self.timeout    = _real_socket.timeout
        self.gaierror   = _real_socket.gaierror
        self.socket           = MagicMock()
        self.create_connection = MagicMock()


class TestSyslogTest:
    def test_no_server_returns_unreachable(self, authed_client):
        resp = authed_client.post("/api/syslog/test", json={"server": ""})
        assert resp.status_code == 200
        assert resp.json()["reachable"] is False

    def test_udp_send_reports_reachable(self, authed_client, monkeypatch):
        import backend.routers.syslog as syslog_module
        fake = _FakeSocketModule()
        mock_sock = MagicMock()
        fake.socket.return_value.__enter__.return_value = mock_sock
        monkeypatch.setattr(syslog_module, "socket", fake)

        resp = authed_client.post("/api/syslog/test", json={
            "server": "10.0.0.5", "port": 514, "protocol": "udp",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["reachable"] is True
        mock_sock.sendto.assert_called_once_with(b"", ("10.0.0.5", 514))

    def test_tcp_connect_reports_reachable(self, authed_client, monkeypatch):
        import backend.routers.syslog as syslog_module
        fake = _FakeSocketModule()
        fake.create_connection.return_value.__enter__.return_value = MagicMock()
        monkeypatch.setattr(syslog_module, "socket", fake)

        resp = authed_client.post("/api/syslog/test", json={
            "server": "10.0.0.5", "port": 6514, "protocol": "tcp",
        })
        assert resp.status_code == 200
        assert resp.json()["reachable"] is True
        fake.create_connection.assert_called_once_with(("10.0.0.5", 6514), timeout=5)

    def test_connection_refused_reports_unreachable(self, authed_client, monkeypatch):
        import backend.routers.syslog as syslog_module
        fake = _FakeSocketModule()
        fake.create_connection.side_effect = ConnectionRefusedError("refused")
        monkeypatch.setattr(syslog_module, "socket", fake)

        resp = authed_client.post("/api/syslog/test", json={
            "server": "10.0.0.5", "port": 6514, "protocol": "tcp",
        })
        assert resp.status_code == 200
        assert resp.json()["reachable"] is False

    def test_requires_auth(self, client):
        resp = client.post("/api/syslog/test", json={"server": "10.0.0.5"})
        assert resp.status_code == 401
