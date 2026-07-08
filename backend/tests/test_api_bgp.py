# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
API tests for GET/PUT /api/bgp and GET /api/bgp/status (issue #143).

vtysh is mocked via a fake bound only onto backend.routers.bgp.subprocess
(same targeted-mock shape as test_nebula_api.py/test_wireguard_api.py — a
blanket subprocess.run replacement would also swallow other modules' calls
in the same request, e.g. config.py's apply-path subprocess calls).
"""
import json

import pytest
from fastapi.testclient import TestClient

import backend.state as state_module
import backend.auth as auth_module
import backend.routers.bgp as bgp_router


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeVtysh:
    """Deterministic stand-in for vtysh -c "show ip bgp summary json" /
    "show ip bgp neighbors json". Configurable per-test via `summary` and
    `neighbors_detail` dicts; `fail` simulates frr being down/uninstalled."""
    def __init__(self, summary=None, neighbors_detail=None, fail=False):
        self.summary = summary if summary is not None else {"ipv4Unicast": {"peers": {}}}
        self.neighbors_detail = neighbors_detail if neighbors_detail is not None else {}
        self.fail = fail
        self.calls = []

    def run(self, cmd, *args, **kwargs):
        self.calls.append(cmd)
        if self.fail:
            return _Result(1, stderr="frr not running")
        joined = cmd[-1] if cmd[0] == "vtysh" else ""
        if "summary" in joined:
            return _Result(0, stdout=json.dumps(self.summary))
        if "neighbors" in joined:
            return _Result(0, stdout=json.dumps(self.neighbors_detail))
        return _Result(1, stderr=f"unexpected command: {cmd}")


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
    import re
    cookie_header = resp.headers.get("set-cookie", "")
    match = re.search(r"spud_token=([^;]+)", cookie_header)
    if match:
        client.cookies.set("spud_token", match.group(1))
    return client


class TestGetBgp:
    def test_default_disabled(self, authed_client):
        resp = authed_client.get("/api/bgp")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert body["neighbors"] == []

    def test_requires_auth(self, client):
        assert client.get("/api/bgp").status_code == 401


class TestPutBgp:
    def test_round_trip_stores_config(self, authed_client):
        body = {
            "enabled": True, "asn": 65001, "router_id": "10.0.0.1",
            "neighbors": [{"ip": "192.168.10.2", "remote_as": 65002, "description": "ISP"}],
            "networks": ["10.0.0.0/24"],
        }
        resp = authed_client.put("/api/bgp", json=body)
        assert resp.status_code == 200

        resp = authed_client.get("/api/bgp")
        got = resp.json()
        assert got["enabled"] is True
        assert got["asn"] == 65001
        assert got["router_id"] == "10.0.0.1"
        assert got["neighbors"][0]["ip"] == "192.168.10.2"
        assert got["networks"] == ["10.0.0.0/24"]

    def test_enabled_without_asn_rejected(self, authed_client):
        resp = authed_client.put("/api/bgp", json={"enabled": True, "asn": None, "router_id": None})
        assert resp.status_code == 422

    def test_invalid_neighbor_ip_rejected(self, authed_client):
        resp = authed_client.put("/api/bgp", json={
            "enabled": False,
            "neighbors": [{"ip": "not-an-ip", "remote_as": 65002}],
        })
        assert resp.status_code == 422

    def test_requires_auth(self, client):
        assert client.put("/api/bgp", json={"enabled": False}).status_code == 401


class TestBgpStatus:
    def test_disabled_returns_not_running_no_vtysh_call(self, authed_client, monkeypatch):
        fake = _FakeVtysh()
        monkeypatch.setattr(bgp_router.subprocess, "run", fake.run)
        resp = authed_client.get("/api/bgp/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"enabled": False, "running": False, "neighbors": []}
        assert fake.calls == []

    def test_enabled_but_frr_down_degrades_gracefully(self, authed_client, monkeypatch):
        authed_client.put("/api/bgp", json={"enabled": True, "asn": 65001, "router_id": "10.0.0.1"})
        fake = _FakeVtysh(fail=True)
        monkeypatch.setattr(bgp_router.subprocess, "run", fake.run)
        resp = authed_client.get("/api/bgp/status")
        assert resp.status_code == 200
        assert resp.json() == {"enabled": True, "running": False, "neighbors": []}

    def test_enabled_and_healthy_reports_neighbor_state(self, authed_client, monkeypatch):
        authed_client.put("/api/bgp", json={
            "enabled": True, "asn": 65001, "router_id": "10.0.0.1",
            "neighbors": [{"ip": "192.168.10.2", "remote_as": 65002, "description": ""}],
        })
        fake = _FakeVtysh(
            summary={"ipv4Unicast": {"peers": {
                "192.168.10.2": {"state": "Established", "pfxRcd": 5, "remoteAs": 65002},
            }}},
            neighbors_detail={
                "192.168.10.2": {"addressFamilyInfo": {"ipv4Unicast": {"sentPrefixCounter": 3}}},
            },
        )
        monkeypatch.setattr(bgp_router.subprocess, "run", fake.run)
        resp = authed_client.get("/api/bgp/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["running"] is True
        assert body["neighbors"] == [{"ip": "192.168.10.2", "state": "Established", "pfx_rcvd": 5, "pfx_sent": 3}]

    def test_requires_auth(self, client):
        assert client.get("/api/bgp/status").status_code == 401
