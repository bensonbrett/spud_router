"""
Tests for GET /api/apply/status — the "pending changes" detection endpoint.

Apply itself writes root-owned files via `sudo tee` and restarts services;
those subprocess calls are mocked here exactly like the existing tailscale
apply tests do, so these tests never touch the real filesystem/services
beyond the tmp_path-isolated state and applied-snapshot files.
"""
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
    monkeypatch.setattr(state_module, "SPUD_CONF",             conf_dir)
    monkeypatch.setattr(state_module, "STATE_FILE",            state_file)
    monkeypatch.setattr(state_module, "APPLIED_SNAPSHOT_FILE", conf_dir / "applied.json")
    monkeypatch.setattr(auth_module,  "AUTH_FILE",             auth_file)
    monkeypatch.setattr(auth_module,  "SPUD_CONF",             conf_dir)
    monkeypatch.setattr(auth_module,  "CLI_TOKEN_FILE",        conf_dir / "cli-token")
    monkeypatch.setattr(auth_module,  "TOKEN_SECRET_FILE",     conf_dir / "token-secret")
    monkeypatch.setattr(auth_module,  "_revoked",              set())

    import backend.routers.config as config_module
    import backend.apply_core as apply_core_module
    monkeypatch.setattr(config_module, "APPLIED_SNAPSHOT_FILE", conf_dir / "applied.json")
    monkeypatch.setattr(state_module, "ROLLBACK_STATE_FILE", conf_dir / "state.rollback.json")
    monkeypatch.setattr(state_module, "ARM_STATUS_FILE",     conf_dir / "arm-status.json")
    monkeypatch.setattr(config_module, "ROLLBACK_STATE_FILE",     conf_dir / "state.rollback.json")
    monkeypatch.setattr(config_module, "LAST_APPLIED_STATE_FILE", conf_dir / "state.last-applied.json")
    monkeypatch.setattr(config_module, "ARM_STATUS_FILE",         conf_dir / "arm-status.json")
    # apply_core.py bound its own copy of this path constant (`from .state
    # import IPTABLES_SCRIPT`), same aliasing gotcha noted elsewhere in this
    # test suite — patching config_module's name wouldn't reach it.
    monkeypatch.setattr(apply_core_module, "IPTABLES_SCRIPT", conf_dir / "iptables.sh")


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


def _mock_ok(*a, **kw):
    m = MagicMock()
    m.returncode = 0
    m.stdout = ""
    m.stderr = ""
    return m


class TestApplyStatus:
    def test_fresh_state_is_pending(self, authed_client):
        resp = authed_client.get("/api/apply/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pending"] is True
        assert body["applied_hash"] is None
        assert body["current_hash"]

    def test_pending_false_after_apply(self, authed_client):
        with patch("backend.routers.config.subprocess.run", side_effect=_mock_ok), \
             patch("backend.routers.tailscale.subprocess.run", side_effect=_mock_ok):
            apply_resp = authed_client.post("/api/apply", json={"dry_run": False})
        assert apply_resp.status_code == 200

        status_resp = authed_client.get("/api/apply/status")
        body = status_resp.json()
        assert body["pending"] is False
        assert body["applied_hash"] == body["current_hash"]

    def test_pending_true_again_after_generator_affecting_change(self, authed_client):
        with patch("backend.routers.config.subprocess.run", side_effect=_mock_ok), \
             patch("backend.routers.tailscale.subprocess.run", side_effect=_mock_ok):
            authed_client.post("/api/apply", json={"dry_run": False})

        assert authed_client.get("/api/apply/status").json()["pending"] is False

        # Add a VLAN — changes the generated netplan/dnsmasq/iptables output.
        authed_client.post("/api/vlans", json={
            "vlan_id": 10, "name": "Trusted", "interface": "eth0",
            "ip_address": "192.168.10.1", "prefix_len": 24,
        })

        assert authed_client.get("/api/apply/status").json()["pending"] is True

    def test_dry_run_apply_does_not_clear_pending(self, authed_client):
        with patch("backend.routers.config.subprocess.run", side_effect=_mock_ok), \
             patch("backend.routers.tailscale.subprocess.run", side_effect=_mock_ok):
            resp = authed_client.post("/api/apply", json={"dry_run": True})
        assert resp.status_code == 200
        assert authed_client.get("/api/apply/status").json()["pending"] is True

    def test_pending_survives_across_requests(self, authed_client):
        """No in-memory state — the snapshot is a file, so this persists
        across reloads exactly like it would across a service restart."""
        with patch("backend.routers.config.subprocess.run", side_effect=_mock_ok), \
             patch("backend.routers.tailscale.subprocess.run", side_effect=_mock_ok):
            authed_client.post("/api/apply", json={"dry_run": False})

        first  = authed_client.get("/api/apply/status").json()
        second = authed_client.get("/api/apply/status").json()
        assert first == second
        assert first["pending"] is False

    def test_requires_auth(self, client):
        resp = client.get("/api/apply/status")
        assert resp.status_code == 401
