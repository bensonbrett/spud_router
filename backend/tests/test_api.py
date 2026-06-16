"""
API integration tests using FastAPI's TestClient.

These tests run against the full application stack (routing, auth, validation)
but with the filesystem isolated to a temp directory so nothing touches
/etc/spud-router on the test machine.
"""
import json
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

import state as state_module
from state import empty_state, save_state
import auth as auth_module


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirect all state I/O and auth to temp dirs for every test."""
    conf_dir   = tmp_path / "spud-router"
    state_file = conf_dir / "state.json"
    auth_file  = conf_dir / "auth.json"
    monkeypatch.setattr(state_module, "SPUD_CONF",   conf_dir)
    monkeypatch.setattr(state_module, "STATE_FILE",  state_file)
    monkeypatch.setattr(auth_module,  "AUTH_FILE",   auth_file)
    monkeypatch.setattr(auth_module,  "SPUD_CONF",   conf_dir)
    monkeypatch.setattr(auth_module,  "CLI_TOKEN_FILE", conf_dir / "cli-token")


@pytest.fixture
def client():
    from main import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def authed_client(client):
    """Client with a valid session token already set."""
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "spudrouter"})
    assert resp.status_code == 200
    token = resp.json()["token"]
    client.headers.update({"X-Session-Token": token})
    return client


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_login_valid_credentials(self, client):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "spudrouter"})
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_login_wrong_password(self, client):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401

    def test_login_wrong_username(self, client):
        resp = client.post("/api/auth/login", json={"username": "hacker", "password": "spudrouter"})
        assert resp.status_code == 401

    def test_protected_endpoint_requires_token(self, client):
        resp = client.get("/api/state")
        assert resp.status_code == 401

    def test_protected_endpoint_accepts_valid_token(self, authed_client):
        resp = authed_client.get("/api/state")
        assert resp.status_code == 200

    def test_logout_invalidates_token(self, client):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "spudrouter"})
        token = resp.json()["token"]
        client.post("/api/auth/logout", headers={"X-Session-Token": token})
        resp = client.get("/api/state", headers={"X-Session-Token": token})
        assert resp.status_code == 401


# ── VLANs ─────────────────────────────────────────────────────────────────────

class TestVlans:
    def test_list_vlans_empty(self, authed_client):
        resp = authed_client.get("/api/vlans")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_add_vlan(self, authed_client):
        resp = authed_client.post("/api/vlans", json={
            "vlan_id": 10, "name": "Trusted", "interface": "eth0",
            "ip_address": "192.168.10.1", "prefix_len": 24,
        })
        assert resp.status_code == 200
        vlans = authed_client.get("/api/vlans").json()
        assert len(vlans) == 1
        assert vlans[0]["vlan_id"] == 10

    def test_add_duplicate_vlan_rejected(self, authed_client):
        payload = {"vlan_id": 10, "name": "A", "interface": "eth0",
                   "ip_address": "192.168.10.1", "prefix_len": 24}
        authed_client.post("/api/vlans", json=payload)
        resp = authed_client.post("/api/vlans", json=payload)
        assert resp.status_code == 400

    def test_add_vlan_invalid_id(self, authed_client):
        resp = authed_client.post("/api/vlans", json={
            "vlan_id": 9999, "name": "Bad", "interface": "eth0",
            "ip_address": "192.168.10.1", "prefix_len": 24,
        })
        assert resp.status_code == 422  # Pydantic validation error

    def test_delete_vlan(self, authed_client):
        authed_client.post("/api/vlans", json={
            "vlan_id": 10, "name": "Trusted", "interface": "eth0",
            "ip_address": "192.168.10.1", "prefix_len": 24,
        })
        resp = authed_client.delete("/api/vlans/10")
        assert resp.status_code == 200
        assert authed_client.get("/api/vlans").json() == []

    def test_delete_nonexistent_vlan_returns_zero_removed(self, authed_client):
        resp = authed_client.delete("/api/vlans/99")
        assert resp.status_code == 200
        assert resp.json()["removed"] == 0


# ── DNS ───────────────────────────────────────────────────────────────────────

class TestDns:
    def test_add_dns_entry(self, authed_client):
        resp = authed_client.post("/api/dns", json={
            "hostname": "nas", "ip": "192.168.10.10", "description": "TrueNAS",
        })
        assert resp.status_code == 200
        entries = authed_client.get("/api/dns").json()
        assert entries[0]["hostname"] == "nas"

    def test_duplicate_hostname_rejected(self, authed_client):
        payload = {"hostname": "nas", "ip": "192.168.10.10", "description": ""}
        authed_client.post("/api/dns", json=payload)
        resp = authed_client.post("/api/dns", json=payload)
        assert resp.status_code == 400

    def test_invalid_ip_rejected(self, authed_client):
        resp = authed_client.post("/api/dns", json={
            "hostname": "nas", "ip": "not-an-ip",
        })
        assert resp.status_code == 422

    def test_delete_dns_entry(self, authed_client):
        authed_client.post("/api/dns", json={"hostname": "nas", "ip": "192.168.10.10"})
        resp = authed_client.delete("/api/dns/nas")
        assert resp.status_code == 200
        assert authed_client.get("/api/dns").json() == []


# ── Static routes ─────────────────────────────────────────────────────────────

class TestRoutes:
    def test_add_route(self, authed_client):
        resp = authed_client.post("/api/routes", json={
            "destination": "10.0.0.0/8", "gateway": "192.168.10.254",
        })
        assert resp.status_code == 200

    def test_duplicate_destination_rejected(self, authed_client):
        payload = {"destination": "10.0.0.0/8", "gateway": "192.168.10.254"}
        authed_client.post("/api/routes", json=payload)
        resp = authed_client.post("/api/routes", json=payload)
        assert resp.status_code == 400

    def test_invalid_cidr_rejected(self, authed_client):
        resp = authed_client.post("/api/routes", json={
            "destination": "not-a-cidr", "gateway": "192.168.10.254",
        })
        assert resp.status_code == 422


# ── Firewall ──────────────────────────────────────────────────────────────────

class TestFirewall:
    def test_add_inbound_rule(self, authed_client):
        resp = authed_client.post("/api/firewall/inbound", json={
            "vlan_id": 10, "proto": "tcp", "port": 22,
            "action": "accept", "description": "SSH",
        })
        assert resp.status_code == 200
        assert "id" in resp.json()

    def test_inbound_rule_has_generated_id(self, authed_client):
        resp = authed_client.post("/api/firewall/inbound", json={
            "vlan_id": 0, "proto": "tcp", "port": 8080,
            "action": "accept", "description": "",
        })
        rule_id = resp.json()["id"]
        assert len(rule_id) == 8   # 4 bytes = 8 hex chars

    def test_delete_inbound_rule(self, authed_client):
        resp = authed_client.post("/api/firewall/inbound", json={
            "vlan_id": 0, "proto": "any", "action": "accept",
        })
        rule_id = resp.json()["id"]
        del_resp = authed_client.delete(f"/api/firewall/inbound/{rule_id}")
        assert del_resp.status_code == 200
        assert authed_client.get("/api/firewall/inbound").json() == []

    def test_invalid_proto_rejected(self, authed_client):
        resp = authed_client.post("/api/firewall/inbound", json={
            "proto": "icmp", "action": "accept",
        })
        assert resp.status_code == 422

    def test_add_intervlan_rule(self, authed_client):
        resp = authed_client.post("/api/firewall/intervlan", json={
            "from_vlan": 10, "to_vlan": 20, "proto": "tcp",
            "port": 443, "action": "accept",
        })
        assert resp.status_code == 200


# ── Config preview ────────────────────────────────────────────────────────────

class TestPreview:
    def test_preview_returns_all_three_configs(self, authed_client):
        resp = authed_client.get("/api/preview")
        assert resp.status_code == 200
        data = resp.json()
        assert "netplan" in data
        assert "dnsmasq" in data
        assert "iptables" in data

    def test_preview_netplan_has_network_header(self, authed_client):
        resp = authed_client.get("/api/preview")
        assert resp.json()["netplan"].startswith("network:")

    def test_preview_iptables_is_bash(self, authed_client):
        resp = authed_client.get("/api/preview")
        assert resp.json()["iptables"].startswith("#!/bin/bash")


# ── Config import / export ────────────────────────────────────────────────────

class TestConfigImport:
    def test_import_valid_state(self, authed_client):
        data = empty_state()
        data["vlans"] = [{"vlan_id": 10, "name": "Imported", "interface": "eth0",
                          "ip_address": "192.168.10.1", "prefix_len": 24,
                          "dhcp_enabled": False, "dhcp_start": "", "dhcp_end": "",
                          "dhcp_lease": "12h", "isolate": False}]
        resp = authed_client.post("/api/config/import", json=data)
        assert resp.status_code == 200
        assert resp.json()["vlans"] == 1

    def test_import_missing_required_key_rejected(self, authed_client):
        resp = authed_client.post("/api/config/import", json={"router": {}})
        assert resp.status_code == 400

    def test_import_backfills_optional_keys(self, authed_client):
        resp = authed_client.post("/api/config/import", json={
            "router": {}, "vlans": [],
        })
        assert resp.status_code == 200
        state = authed_client.get("/api/state").json()
        assert "fw_inbound" in state
        assert "dns_entries" in state
