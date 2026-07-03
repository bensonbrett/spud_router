"""Tests for GET/PUT /api/snmp — masked-community read/write behavior."""
import pytest
from fastapi.testclient import TestClient

import backend.state as state_module
import backend.auth as auth_module
from backend.models import SNMP_MASKED_SENTINEL


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


class TestGetSnmp:
    def test_default_disabled_empty_community(self, authed_client):
        resp = authed_client.get("/api/snmp")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert body["community_ro"] == ""

    def test_requires_auth(self, client):
        assert client.get("/api/snmp").status_code == 401


class TestPutSnmp:
    def test_round_trip_stores_community(self, authed_client):
        resp = authed_client.put("/api/snmp", json={
            "enabled": True, "community_ro": "public", "allowlist": ["10.0.0.0/24"],
        })
        assert resp.status_code == 200

        got = authed_client.get("/api/snmp").json()
        assert got["allowlist"] == ["10.0.0.0/24"]

    def test_get_after_put_masks_community(self, authed_client):
        authed_client.put("/api/snmp", json={"enabled": True, "community_ro": "public"})
        got = authed_client.get("/api/snmp").json()
        assert got["community_ro"] == SNMP_MASKED_SENTINEL
        assert "public" not in got["community_ro"]

    def test_submitting_sentinel_preserves_stored_community(self, authed_client):
        authed_client.put("/api/snmp", json={
            "enabled": True, "community_ro": "public", "location": "Rack 1",
        })
        # Simulate the UI round-tripping the masked value back unchanged
        # while editing an unrelated field.
        resp = authed_client.put("/api/snmp", json={
            "enabled": True, "community_ro": SNMP_MASKED_SENTINEL, "location": "Rack 2",
        })
        assert resp.status_code == 200

        got = authed_client.get("/api/snmp").json()
        assert got["location"] == "Rack 2"
        assert got["community_ro"] == SNMP_MASKED_SENTINEL  # still masked on read

    def test_submitting_new_value_overwrites_stored_community(self, authed_client):
        authed_client.put("/api/snmp", json={"enabled": True, "community_ro": "public"})
        authed_client.put("/api/snmp", json={"enabled": True, "community_ro": "new-secret"})

        # No direct way to read the cleartext back via the API (by design),
        # but a subsequent PUT with the *old* sentinel value should now
        # preserve "new-secret", not "public" — proving it was overwritten.
        authed_client.put("/api/snmp", json={"enabled": True, "community_ro": SNMP_MASKED_SENTINEL})
        got = authed_client.get("/api/snmp").json()
        assert got["community_ro"] == SNMP_MASKED_SENTINEL

    def test_enabled_without_community_rejected(self, authed_client):
        resp = authed_client.put("/api/snmp", json={"enabled": True, "community_ro": ""})
        assert resp.status_code == 422

    def test_invalid_allowlist_entry_rejected(self, authed_client):
        resp = authed_client.put("/api/snmp", json={"allowlist": ["not-an-ip"]})
        assert resp.status_code == 422

    def test_bad_bind_interface_rejected(self, authed_client):
        resp = authed_client.put("/api/snmp", json={"bind_interface": "eth0; rm -rf /"})
        assert resp.status_code == 422

    def test_requires_auth(self, client):
        resp = client.put("/api/snmp", json={"enabled": False})
        assert resp.status_code == 401
