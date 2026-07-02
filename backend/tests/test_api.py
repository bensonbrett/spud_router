"""
API integration tests using FastAPI's TestClient.

These tests run against the full application stack (routing, auth, validation)
but with the filesystem isolated to a temp directory so nothing touches
/etc/spud-router on the test machine.
"""
import json
import time
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

import backend.state as state_module
from backend.state import empty_state, save_state
import backend.auth as auth_module
from backend.auth import create_token, is_valid_token, revoke_token
import backend.routers.tailscale as tailscale_module


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirect all state I/O and auth to temp dirs for every test."""
    conf_dir   = tmp_path / "spud-router"
    state_file = conf_dir / "state.json"
    auth_file  = conf_dir / "auth.json"
    monkeypatch.setattr(state_module, "SPUD_CONF",          conf_dir)
    monkeypatch.setattr(state_module, "STATE_FILE",         state_file)
    monkeypatch.setattr(auth_module,  "AUTH_FILE",          auth_file)
    monkeypatch.setattr(auth_module,  "SPUD_CONF",          conf_dir)
    monkeypatch.setattr(auth_module,  "CLI_TOKEN_FILE",     conf_dir / "cli-token")
    monkeypatch.setattr(auth_module,  "TOKEN_SECRET_FILE",  conf_dir / "token-secret")
    monkeypatch.setattr(auth_module,  "_revoked",           set())
    monkeypatch.setattr(tailscale_module, "TAILSCALE_AUTHKEY_FILE", conf_dir / "tailscale-authkey")


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def authed_client(client):
    """Client with a valid session token already set."""
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "spudrouter"})
    assert resp.status_code == 200
    token = resp.json()["token"]
    client.headers.update({"X-Session-Token": token})
    return client


# ── CLI service token ──────────────────────────────────────────────────────────
# The spud-cli TUI authenticates with the long-lived token install.sh writes to
# /etc/spud-router/cli-token (group-readable by the 'spud' user). auth.py accepts
# any request whose token matches that file — no admin login required.

class TestCliServiceToken:
    def test_cli_token_authorizes_without_login(self, client):
        token = "a" * 64
        auth_module.CLI_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        auth_module.CLI_TOKEN_FILE.write_text(token + "\n")   # trailing newline, like a file write
        client.headers.update({"X-Session-Token": token})
        assert client.get("/api/state").status_code == 200

    def test_wrong_cli_token_rejected(self, client):
        auth_module.CLI_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        auth_module.CLI_TOKEN_FILE.write_text("the-real-token\n")
        client.headers.update({"X-Session-Token": "not-the-token"})
        assert client.get("/api/state").status_code == 401


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_login_valid_credentials(self, client):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "spudrouter"})
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_login_cookie_has_secure_httponly_samesite(self, client):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "spudrouter"})
        assert resp.status_code == 200
        cookie_header = resp.headers.get("set-cookie", "")
        assert "spud_token=" in cookie_header
        assert "Secure" in cookie_header
        assert "HttpOnly" in cookie_header
        assert "SameSite=strict" in cookie_header.lower() or "samesite=strict" in cookie_header.lower()

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


# ── HMAC token unit tests ─────────────────────────────────────────────────────

class TestTokenHMAC:
    def test_token_roundtrip(self):
        token = create_token()
        assert is_valid_token(token)

    def test_expired_token_rejected(self):
        # Force exp into the past by patching time.time during creation
        with patch("backend.auth.time") as mock_time:
            mock_time.time.return_value = time.time() - 9 * 3600  # 9 hours ago
            token = create_token()
        assert not is_valid_token(token)

    def test_tampered_sig_rejected(self):
        token = create_token()
        nonce, exp, sig = token.split(".")
        bad_sig = ("A" if sig[0] != "A" else "B") + sig[1:]
        assert not is_valid_token(f"{nonce}.{exp}.{bad_sig}")

    def test_malformed_token_rejected(self):
        assert not is_valid_token("not.a.valid.token.format")
        assert not is_valid_token("onlytwoparts.here")
        assert not is_valid_token("")

    def test_revoked_token_rejected(self):
        token = create_token()
        assert is_valid_token(token)
        revoke_token(token)
        assert not is_valid_token(token)


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

    def test_update_vlan(self, authed_client):
        authed_client.post("/api/vlans", json={
            "vlan_id": 10, "name": "Trusted", "interface": "eth0",
            "ip_address": "192.168.10.1", "prefix_len": 24,
            "dhcp_enabled": True, "dhcp_start": "192.168.10.100", "dhcp_end": "192.168.10.200",
        })
        resp = authed_client.put("/api/vlans/10", json={
            "vlan_id": 10, "name": "Renamed", "interface": "eth0",
            "ip_address": "192.168.20.1", "prefix_len": 24,
            "dhcp_enabled": False, "dns_server": "1.1.1.1", "dhcp_options": ["42,192.168.20.1"],
        })
        assert resp.status_code == 200
        vlans = authed_client.get("/api/vlans").json()
        assert len(vlans) == 1
        v = vlans[0]
        assert v["name"] == "Renamed"
        assert v["ip_address"] == "192.168.20.1"
        assert v["dhcp_enabled"] is False
        assert v["dns_server"] == "1.1.1.1"
        assert v["dhcp_options"] == ["42,192.168.20.1"]

    def test_update_vlan_id_mismatch_rejected(self, authed_client):
        authed_client.post("/api/vlans", json={
            "vlan_id": 10, "name": "Trusted", "interface": "eth0",
            "ip_address": "192.168.10.1", "prefix_len": 24,
        })
        resp = authed_client.put("/api/vlans/10", json={
            "vlan_id": 11, "name": "Trusted", "interface": "eth0",
            "ip_address": "192.168.10.1", "prefix_len": 24,
        })
        assert resp.status_code == 400

    def test_update_nonexistent_vlan_returns_404(self, authed_client):
        resp = authed_client.put("/api/vlans/99", json={
            "vlan_id": 99, "name": "Ghost", "interface": "eth0",
            "ip_address": "192.168.10.1", "prefix_len": 24,
        })
        assert resp.status_code == 404

    def test_update_vlan_conflicting_interface_rejected(self, authed_client):
        authed_client.post("/api/vlans", json={
            "vlan_id": 10, "name": "A", "interface": "eth0",
            "ip_address": "192.168.10.1", "prefix_len": 24,
        })
        authed_client.post("/api/vlans", json={
            "vlan_id": 10, "name": "B", "interface": "eth1",
            "ip_address": "192.168.11.1", "prefix_len": 24,
        })
        resp = authed_client.put("/api/vlans/10", json={
            "vlan_id": 10, "name": "A", "interface": "eth1",
            "ip_address": "192.168.10.1", "prefix_len": 24,
        })
        assert resp.status_code == 400

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


class TestOutboundFirewall:
    def test_list_empty(self, authed_client):
        resp = authed_client.get("/api/firewall/outbound")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_add_outbound_rule_returns_id(self, authed_client):
        resp = authed_client.post("/api/firewall/outbound", json={
            "vlan_id": 20, "dest": "", "proto": "any",
            "action": "drop", "description": "Block IoT internet",
        })
        assert resp.status_code == 200
        rule_id = resp.json()["id"]
        assert len(rule_id) == 8   # 4 bytes = 8 hex chars

    def test_add_outbound_rule_reflected_in_list(self, authed_client):
        authed_client.post("/api/firewall/outbound", json={
            "vlan_id": 10, "dest": "8.8.8.8", "proto": "udp", "port": 53,
            "action": "accept", "description": "Allow DNS",
        })
        rules = authed_client.get("/api/firewall/outbound").json()
        assert len(rules) == 1
        assert rules[0]["dest"] == "8.8.8.8"
        assert rules[0]["vlan_id"] == 10

    def test_delete_outbound_rule(self, authed_client):
        resp = authed_client.post("/api/firewall/outbound", json={
            "vlan_id": 0, "dest": "", "proto": "any", "action": "drop",
        })
        rule_id = resp.json()["id"]
        del_resp = authed_client.delete(f"/api/firewall/outbound/{rule_id}")
        assert del_resp.status_code == 200
        assert authed_client.get("/api/firewall/outbound").json() == []

    def test_delete_nonexistent_outbound_rule_returns_zero_removed(self, authed_client):
        resp = authed_client.delete("/api/firewall/outbound/deadbeef")
        assert resp.status_code == 200
        assert resp.json()["removed"] == 0

    def test_invalid_dest_rejected(self, authed_client):
        resp = authed_client.post("/api/firewall/outbound", json={
            "vlan_id": 10, "dest": "not-an-ip", "action": "accept",
        })
        assert resp.status_code == 422

    def test_invalid_proto_rejected(self, authed_client):
        resp = authed_client.post("/api/firewall/outbound", json={
            "vlan_id": 10, "proto": "icmp", "action": "accept",
        })
        assert resp.status_code == 422

    def test_default_starts_allow(self, authed_client):
        resp = authed_client.get("/api/firewall/outbound/default")
        assert resp.status_code == 200
        assert resp.json() == {"default": "allow"}

    def test_set_default_to_deny(self, authed_client):
        resp = authed_client.put("/api/firewall/outbound/default", json={"default": "deny"})
        assert resp.status_code == 200
        assert authed_client.get("/api/firewall/outbound/default").json() == {"default": "deny"}

    def test_set_default_invalid_value_rejected(self, authed_client):
        resp = authed_client.put("/api/firewall/outbound/default", json={"default": "block"})
        assert resp.status_code == 422


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
        assert "fw_outbound" in state
        assert state["fw_outbound_default"] == "allow"

    def test_import_outbound_rules_and_default(self, authed_client):
        resp = authed_client.post("/api/config/import", json={
            "router": {}, "vlans": [],
            "fw_outbound": [{"vlan_id": 10, "dest": "8.8.8.8", "proto": "udp",
                              "port": 53, "action": "accept", "description": ""}],
            "fw_outbound_default": "deny",
        })
        assert resp.status_code == 200
        assert resp.json()["fw_outbound"] == 1
        state = authed_client.get("/api/state").json()
        assert state["fw_outbound_default"] == "deny"
        assert state["fw_outbound"][0]["dest"] == "8.8.8.8"


# ── Diagnostics ───────────────────────────────────────────────────────────────

class TestDiagnostics:
    def test_diagnostics_returns_expected_keys(self, authed_client):
        resp = authed_client.get("/api/diagnostics")
        assert resp.status_code == 200
        data = resp.json()
        assert "vlans" in data
        assert "default_route" in data

    def test_diagnostics_no_vlans_returns_empty_list(self, authed_client):
        resp = authed_client.get("/api/diagnostics")
        assert resp.json()["vlans"] == []

    def test_diagnostics_with_vlan_returns_iface_info(self, authed_client):
        authed_client.post("/api/vlans", json={
            "vlan_id": 10, "name": "Trusted", "interface": "eth0",
            "ip_address": "192.168.10.1", "prefix_len": 24,
        })
        resp = authed_client.get("/api/diagnostics")
        assert resp.status_code == 200
        vlans = resp.json()["vlans"]
        assert len(vlans) == 1
        v = vlans[0]
        assert v["name"] == "eth0.10"
        assert v["vlan_id"] == 10
        assert v["vlan_name"] == "Trusted"
        assert v["cfg_address"] == "192.168.10.1/24"
        assert "carrier" in v
        assert "operstate" in v
        assert "addresses" in v
        assert "leases" in v

    def test_diagnostics_with_wan(self, authed_client):
        authed_client.post("/api/router", json={
            "wan_interface": "eth0.2", "wan_mode": "dhcp",
            "hostname": "spud-router",
        })
        resp = authed_client.get("/api/diagnostics")
        assert resp.status_code == 200
        wan = resp.json()["wan"]
        assert wan is not None
        assert wan["name"] == "eth0.2"
        assert wan["role"] == "wan"
        assert "carrier" in wan
        assert "addresses" in wan

    def test_diagnostics_no_wan_when_router_unconfigured(self, authed_client):
        resp = authed_client.get("/api/diagnostics")
        assert resp.status_code == 200
        # wan is None or absent when no wan_interface configured
        wan = resp.json().get("wan")
        assert wan is None

    def test_diagnostics_vlan_without_static_ip(self, authed_client):
        # Mirrors the installer's default WAN VLAN, which has no static IP
        # (ip_address="", prefix_len=0) because it is addressed via DHCP.
        # Such a VLAN must not report a bogus "/0" configured address, and
        # its empty subnet prefix must not match every DHCP lease.
        state = empty_state()
        state["vlans"] = [
            {"vlan_id": 2, "name": "WAN", "interface": "end0",
             "ip_address": "", "prefix_len": 0, "dhcp_enabled": False,
             "dhcp_start": "", "dhcp_end": "", "dhcp_lease": "12h", "isolate": False},
            {"vlan_id": 10, "name": "LAN", "interface": "end0",
             "ip_address": "192.168.10.1", "prefix_len": 24, "dhcp_enabled": True,
             "dhcp_start": "192.168.10.100", "dhcp_end": "192.168.10.200",
             "dhcp_lease": "12h", "isolate": False},
        ]
        save_state(state)

        vlans = {v["vlan_id"]: v for v in authed_client.get("/api/diagnostics").json()["vlans"]}

        wan_vlan = vlans[2]
        assert wan_vlan["cfg_address"] is None      # not "/0"
        assert wan_vlan["ip_present"] is False
        assert wan_vlan["leases"] == []             # empty prefix must not match all leases

        lan_vlan = vlans[10]
        assert lan_vlan["cfg_address"] == "192.168.10.1/24"


# ── Tailscale ─────────────────────────────────────────────────────────────────

class TestTailscaleAuthKey:
    def test_set_authkey_valid(self, authed_client):
        resp = authed_client.post("/api/tailscale/authkey", json={"auth_key": "tskey-auth-abc123"})
        assert resp.status_code == 200

        assert tailscale_module.TAILSCALE_AUTHKEY_FILE.exists()
        assert oct(tailscale_module.TAILSCALE_AUTHKEY_FILE.stat().st_mode)[-3:] == "600"

        cfg = authed_client.get("/api/tailscale").json()
        assert cfg["has_auth_key"] is True
        assert "auth_key" not in cfg
        assert "tskey-auth-abc123" not in json.dumps(cfg)

    def test_set_authkey_invalid_rejected(self, authed_client):
        resp = authed_client.post("/api/tailscale/authkey", json={"auth_key": "not-a-key"})
        assert resp.status_code == 422
        assert not tailscale_module.TAILSCALE_AUTHKEY_FILE.exists()

    def test_set_authkey_empty_rejected(self, authed_client):
        resp = authed_client.post("/api/tailscale/authkey", json={"auth_key": ""})
        assert resp.status_code == 422
        assert not tailscale_module.TAILSCALE_AUTHKEY_FILE.exists()

    def test_delete_authkey(self, authed_client):
        authed_client.post("/api/tailscale/authkey", json={"auth_key": "tskey-auth-abc123"})
        resp = authed_client.delete("/api/tailscale/authkey")
        assert resp.status_code == 200

        cfg = authed_client.get("/api/tailscale").json()
        assert cfg["has_auth_key"] is False
        assert not tailscale_module.TAILSCALE_AUTHKEY_FILE.exists()

    def test_authkey_absent_from_state_and_export(self, authed_client):
        authed_client.post("/api/tailscale/authkey", json={"auth_key": "tskey-auth-supersecret"})

        state = authed_client.get("/api/state").json()
        assert "tskey-auth-supersecret" not in json.dumps(state)
        assert "auth_key" not in state.get("tailscale", {})

        export_resp = authed_client.get("/api/config/export")
        assert b"tskey-auth-supersecret" not in export_resp.content

    def test_authkey_apply_immediately_when_enabled(self, authed_client):
        authed_client.post("/api/tailscale", json={
            "enabled": True, "advertise_routes": [], "exit_node": False, "accept_routes": True,
        })
        with patch("backend.routers.tailscale.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            resp = authed_client.post("/api/tailscale/authkey", json={"auth_key": "tskey-auth-abc123"})
        assert resp.status_code == 200
        assert "steps" in resp.json()
        cmd = mock_run.call_args[0][0]
        assert any(a.startswith("--auth-key=file:") for a in cmd)

    def test_authkey_not_applied_when_disabled(self, authed_client):
        with patch("backend.routers.tailscale.subprocess.run") as mock_run:
            resp = authed_client.post("/api/tailscale/authkey", json={"auth_key": "tskey-auth-abc123"})
        assert resp.status_code == 200
        assert "steps" not in resp.json()
        mock_run.assert_not_called()


class TestTailscaleCandidateRoutes:
    def test_candidate_routes_from_vlans_and_mgmt(self, authed_client):
        # WAN VLAN (ip_address="", prefix_len=0) mirrors the installer's default
        # and can't go through POST /api/vlans (VlanConfig requires prefix 1-30),
        # so it's written directly to state, same as test_diagnostics_vlan_without_static_ip.
        state = empty_state()
        state["vlans"] = [
            {"vlan_id": 2, "name": "WAN", "interface": "eth0",
             "ip_address": "", "prefix_len": 0, "dhcp_enabled": False,
             "dhcp_start": "", "dhcp_end": "", "dhcp_lease": "12h", "isolate": False},
            {"vlan_id": 10, "name": "LAN", "interface": "eth0",
             "ip_address": "192.168.10.1", "prefix_len": 24, "dhcp_enabled": True,
             "dhcp_start": "192.168.10.100", "dhcp_end": "192.168.10.200",
             "dhcp_lease": "12h", "isolate": False},
        ]
        state["router"] = {
            "wan_interface": "eth0.2", "wan_mode": "dhcp", "hostname": "spud-router",
            "mgmt_enabled": True, "mgmt_interface": "eth1",
            "mgmt_ip": "192.168.1.1", "mgmt_prefix": 24,
            "mgmt_dhcp_start": "192.168.1.100", "mgmt_dhcp_end": "192.168.1.150",
            "mgmt_dhcp_lease": "12h",
        }
        save_state(state)

        resp = authed_client.get("/api/tailscale/candidate-routes")
        assert resp.status_code == 200
        cidrs = {c["cidr"] for c in resp.json()}
        assert cidrs == {"192.168.10.0/24", "192.168.1.0/24"}

    def test_candidate_routes_empty_with_no_vlans(self, authed_client):
        resp = authed_client.get("/api/tailscale/candidate-routes")
        assert resp.status_code == 200
        assert resp.json() == []
