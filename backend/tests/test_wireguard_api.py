# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
API tests for GET/PUT /api/wireguard, POST /api/wireguard/regenerate-key,
and the peer CRUD endpoints.

The `wg` CLI is mocked throughout (it may not be installed on the test
machine) via a fake that returns deterministic, valid-shaped keys rather
than a blanket subprocess.run replacement — see _FakeWg's docstring for
why a naive blanket mock is the wrong tool here (same class of gotcha as
this suite's other subprocess-mocking notes).
"""
import base64
import os

import pytest
from fastapi.testclient import TestClient

import backend.state as state_module
import backend.auth as auth_module
import backend.routers.wireguard as wireguard_router


def _fake_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


class _FakeWg:
    """
    Deterministic stand-in for the `wg` CLI: genkey returns a fresh random
    key, pubkey returns a fixed derived value keyed by the input so tests
    can assert on it. Bound only onto the name `backend.routers.wireguard
    .subprocess` for the duration of a test (see conftest pattern elsewhere
    in this suite) rather than patching the real global `subprocess.run` —
    this endpoint's own logic calls `wg genkey`/`wg pubkey` multiple times
    per request, and a blanket canned-response mock can't tell those calls
    apart to return different keys for different peers.
    """
    def __init__(self):
        self.pubkey_of = {}

    def run(self, cmd, *a, input=None, **k):
        m = _Result()
        if cmd == ["wg", "genkey"]:
            m.returncode = 0
            m.stdout = _fake_key() + "\n"
            m.stderr = ""
        elif cmd == ["wg", "pubkey"]:
            derived = self.pubkey_of.setdefault(input, _fake_key())
            m.returncode = 0
            m.stdout = derived + "\n"
            m.stderr = ""
        else:
            m.returncode = 1
            m.stdout = ""
            m.stderr = f"unexpected command: {cmd}"
        return m


class _Result:
    returncode = 0
    stdout = ""
    stderr = ""


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    conf_dir   = tmp_path / "spud-router"
    state_file = conf_dir / "state.json"
    auth_file  = conf_dir / "auth.json"
    monkeypatch.setattr(state_module, "SPUD_CONF",  conf_dir)
    monkeypatch.setattr(state_module, "STATE_FILE", state_file)
    monkeypatch.setattr(auth_module,  "AUTH_FILE",  auth_file)
    monkeypatch.setattr(auth_module,  "SPUD_CONF",  conf_dir)
    monkeypatch.setattr(auth_module,  "CLI_TOKEN_FILE", conf_dir / "cli-token")
    monkeypatch.setattr(auth_module,  "TOKEN_SECRET_FILE", conf_dir / "token-secret")
    monkeypatch.setattr(auth_module,  "_revoked", set())

    fake = _FakeWg()
    monkeypatch.setattr(wireguard_router.subprocess, "run", fake.run)
    return {"fake": fake}


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def authed_client(client):
    """Client with a valid session token already set."""
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "spudrouter"})
    assert resp.status_code == 200
    # Extract the token from the Set-Cookie header and set it on the client
    # (TestClient uses HTTP by default, so Secure cookies aren't sent automatically)
    import re
    cookie_header = resp.headers.get("set-cookie", "")
    match = re.search(r"spud_token=([^;]+)", cookie_header)
    if match:
        client.cookies.set("spud_token", match.group(1))
    return client


class TestGetConfig:
    def test_default_disabled_no_key(self, authed_client):
        resp = authed_client.get("/api/wireguard")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert body["has_key"] is False
        assert body["private_key"] == ""

    def test_requires_auth(self, client):
        assert client.get("/api/wireguard").status_code == 401


class TestSetConfig:
    def test_enabling_without_key_auto_generates_one(self, authed_client):
        resp = authed_client.put("/api/wireguard", json={
            "enabled": True, "mode": "server", "listen_port": 51820, "address": "10.100.0.1/24",
        })
        assert resp.status_code == 200

        got = authed_client.get("/api/wireguard").json()
        assert got["has_key"] is True
        assert got["private_key"] == "********"
        assert got["public_key"]

    def test_pasting_a_real_key_derives_pubkey(self, authed_client):
        real_key = _fake_key()
        resp = authed_client.put("/api/wireguard", json={
            "enabled": True, "private_key": real_key, "address": "10.100.0.1/24",
        })
        assert resp.status_code == 200
        got = authed_client.get("/api/wireguard").json()
        assert got["public_key"]

    def test_submitting_sentinel_preserves_stored_key(self, authed_client):
        authed_client.put("/api/wireguard", json={"enabled": True, "address": "10.100.0.1/24"})
        first_pub = authed_client.get("/api/wireguard").json()["public_key"]

        resp = authed_client.put("/api/wireguard", json={
            "enabled": True, "private_key": "********", "address": "10.100.0.2/24",
        })
        assert resp.status_code == 200
        got = authed_client.get("/api/wireguard").json()
        assert got["public_key"] == first_pub
        assert got["address"] == "10.100.0.2/24"

    def test_disabling_clears_public_key_but_not_stored_private_key_view(self, authed_client):
        authed_client.put("/api/wireguard", json={"enabled": True, "address": "10.100.0.1/24"})
        resp = authed_client.put("/api/wireguard", json={"enabled": False, "address": "10.100.0.1/24"})
        assert resp.status_code == 200
        got = authed_client.get("/api/wireguard").json()
        assert got["has_key"] is False

    def test_invalid_private_key_rejected(self, authed_client):
        resp = authed_client.put("/api/wireguard", json={"private_key": "not-a-valid-key"})
        assert resp.status_code == 422

    def test_invalid_mode_rejected(self, authed_client):
        resp = authed_client.put("/api/wireguard", json={"mode": "bogus"})
        assert resp.status_code == 422

    def test_coexistence_rejected_with_tailscale_exit_node(self, authed_client):
        authed_client.post("/api/tailscale", json={
            "enabled": True, "exit_node": True, "advertise_routes": [], "accept_routes": True,
        })
        resp = authed_client.put("/api/wireguard", json={
            "enabled": True, "mode": "client", "address": "10.100.0.2/32",
            "peers": [{"public_key": _fake_key(), "allowed_ips": ["0.0.0.0/0"]}],
        })
        assert resp.status_code == 400
        assert "Only one VPN provider" in resp.json()["detail"]

    def test_requires_auth(self, client):
        assert client.put("/api/wireguard", json={}).status_code == 401


class TestRegenerateKey:
    def test_regenerate_issues_new_key(self, authed_client):
        authed_client.put("/api/wireguard", json={"enabled": True, "address": "10.100.0.1/24"})
        first_pub = authed_client.get("/api/wireguard").json()["public_key"]

        resp = authed_client.post("/api/wireguard/regenerate-key")
        assert resp.status_code == 200
        new_pub = resp.json()["public_key"]
        assert new_pub != first_pub
        assert authed_client.get("/api/wireguard").json()["public_key"] == new_pub

    def test_requires_auth(self, client):
        assert client.post("/api/wireguard/regenerate-key").status_code == 401


class TestPeers:
    def test_list_empty_by_default(self, authed_client):
        assert authed_client.get("/api/wireguard/peers").json() == []

    def test_add_peer_with_own_public_key_no_private_key_returned(self, authed_client):
        pubkey = _fake_key()
        resp = authed_client.post("/api/wireguard/peers", json={
            "name": "phone", "public_key": pubkey, "allowed_ips": ["10.100.0.2/32"],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["peer"]["public_key"] == pubkey
        assert "private_key" not in body
        assert "client_config" not in body

        peers = authed_client.get("/api/wireguard/peers").json()
        assert len(peers) == 1
        assert peers[0]["name"] == "phone"

    def test_add_peer_without_public_key_generates_and_returns_private_key_once(self, authed_client):
        authed_client.put("/api/wireguard", json={
            "enabled": True, "mode": "server", "listen_port": 51820, "address": "10.100.0.1/24",
        })
        resp = authed_client.post("/api/wireguard/peers", json={
            "name": "laptop", "client_address": "10.100.0.2/32",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["private_key"]
        assert "PrivateKey" in body["client_config"]
        assert body["peer"]["public_key"]

        # The private key must never be persisted in state — only the
        # peer's public key is stored.
        stored = authed_client.get("/api/wireguard/peers").json()
        assert body["private_key"] not in str(stored)

    def test_generating_without_client_address_rejected(self, authed_client):
        resp = authed_client.post("/api/wireguard/peers", json={"name": "laptop"})
        assert resp.status_code == 422

    def test_delete_peer(self, authed_client):
        pubkey = _fake_key()
        add_resp = authed_client.post("/api/wireguard/peers", json={
            "name": "phone", "public_key": pubkey, "allowed_ips": ["10.100.0.2/32"],
        })
        peer_id = add_resp.json()["peer"]["id"]

        del_resp = authed_client.delete(f"/api/wireguard/peers/{peer_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["removed"] == 1
        assert authed_client.get("/api/wireguard/peers").json() == []

    def test_requires_auth(self, client):
        assert client.get("/api/wireguard/peers").status_code == 401
