# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
API tests for /api/mcp/{status,config,enable} and PUT /api/mcp/config
(issue #236 — MCP server config editing).
"""
import pytest
from fastapi.testclient import TestClient

import backend.auth as auth_module
import backend.state as state_module
import backend.routers.mcp_mgmt as mcp_mgmt_module
import backend.api_keys as api_keys_module


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    conf_dir = tmp_path / "spud-router"
    monkeypatch.setattr(state_module, "SPUD_CONF", conf_dir)
    monkeypatch.setattr(state_module, "STATE_FILE", conf_dir / "state.json")
    monkeypatch.setattr(auth_module, "AUTH_FILE", conf_dir / "auth.json")
    monkeypatch.setattr(auth_module, "SPUD_CONF", conf_dir)
    monkeypatch.setattr(auth_module, "CLI_TOKEN_FILE", conf_dir / "cli-token")
    monkeypatch.setattr(auth_module, "TOKEN_SECRET_FILE", conf_dir / "token-secret")
    monkeypatch.setattr(auth_module, "_revoked", set())
    monkeypatch.setattr(api_keys_module, "SPUD_CONF", conf_dir)
    monkeypatch.setattr(api_keys_module, "API_KEYS_FILE", conf_dir / "api-keys.json")
    monkeypatch.setattr(api_keys_module, "_validation_failures", {})
    monkeypatch.setattr(mcp_mgmt_module, "SPUD_CONF", conf_dir)
    monkeypatch.setattr(mcp_mgmt_module, "MCP_CONFIG_FILE", conf_dir / "mcp-config.json")


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def authed_client(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "spudrouter"})
    assert resp.status_code == 200
    import re
    match = re.search(r"spud_token=([^;]+)", resp.headers.get("set-cookie", ""))
    if match:
        client.cookies.set("spud_token", match.group(1))
    return client


class TestMcpConfigUpdate:
    def test_put_config_before_enable_returns_404(self, authed_client):
        resp = authed_client.put("/api/mcp/config", json={
            "base_url": "https://10.0.0.1:8080", "tls_verify": True,
            "read_only": True, "confirm_window_seconds": 60,
        })
        assert resp.status_code == 404

    def test_put_config_updates_fields_and_persists(self, authed_client):
        enable_resp = authed_client.post("/api/mcp/enable")
        assert enable_resp.status_code == 200
        original_key_id = authed_client.get("/api/mcp/config").json()["api_key_id"]

        put_resp = authed_client.put("/api/mcp/config", json={
            "base_url": "https://10.0.0.5:9443", "tls_verify": True,
            "read_only": True, "confirm_window_seconds": 60,
        })
        assert put_resp.status_code == 200
        body = put_resp.json()
        assert body["base_url"] == "https://10.0.0.5:9443"
        assert body["tls_verify"] is True
        assert body["read_only"] is True
        assert body["confirm_window_seconds"] == 60
        assert body["api_key_id"] == original_key_id

        get_resp = authed_client.get("/api/mcp/config")
        assert get_resp.json()["base_url"] == "https://10.0.0.5:9443"
        assert get_resp.json()["read_only"] is True

    def test_put_config_preserves_api_key(self, authed_client):
        enable_resp = authed_client.post("/api/mcp/enable")
        plaintext_key = enable_resp.json()["key"]

        authed_client.put("/api/mcp/config", json={
            "base_url": "https://127.0.0.1:8080", "tls_verify": False,
            "read_only": False, "confirm_window_seconds": 30,
        })

        raw_config = mcp_mgmt_module._load_mcp_config()
        assert raw_config["api_key"] == plaintext_key

    def test_put_config_read_only_true_actually_persists(self, authed_client):
        authed_client.post("/api/mcp/enable")
        authed_client.put("/api/mcp/config", json={
            "base_url": "https://127.0.0.1:8080", "tls_verify": False,
            "read_only": True, "confirm_window_seconds": 120,
        })
        status_resp = authed_client.get("/api/mcp/status")
        assert status_resp.json()["read_only"] is True

    def test_put_config_negative_confirm_window_rejected(self, authed_client):
        authed_client.post("/api/mcp/enable")
        resp = authed_client.put("/api/mcp/config", json={
            "base_url": "https://127.0.0.1:8080", "tls_verify": False,
            "read_only": False, "confirm_window_seconds": -1,
        })
        assert resp.status_code == 422

    def test_put_config_requires_auth(self, client):
        resp = client.put("/api/mcp/config", json={
            "base_url": "https://127.0.0.1:8080", "tls_verify": False,
            "read_only": False, "confirm_window_seconds": 120,
        })
        assert resp.status_code == 401
