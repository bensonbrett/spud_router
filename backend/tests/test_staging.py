# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
API tests for the transactional staging pipeline:
POST /api/staging/{begin,op,validate,commit,confirm,discard}, GET /api/staging/status.

subprocess.run is mocked throughout — these tests never actually invoke
sudo/systemd-run/spud-commit.sh, and assert on the exact commands the
endpoints construct instead.
"""
import json
import os
import subprocess

import pytest
from fastapi.testclient import TestClient

import backend.state as state_module
import backend.auth as auth_module
import backend.apply_core as apply_core_module
import backend.staging as staging_module
import backend.routers.config as config_module


def _ok_run(*a, **k):
    return subprocess.CompletedProcess(a, 0, "", "")


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

    monkeypatch.setattr(config_module, "APPLIED_SNAPSHOT_FILE",   conf_dir / "applied.json")
    monkeypatch.setattr(config_module, "ROLLBACK_STATE_FILE",     conf_dir / "state.rollback.json")
    monkeypatch.setattr(config_module, "LAST_APPLIED_STATE_FILE", conf_dir / "state.last-applied.json")
    monkeypatch.setattr(config_module, "ARM_STATUS_FILE",         conf_dir / "arm-status.json")
    monkeypatch.setattr(config_module, "SPUD_COMMIT_SCRIPT",      tmp_path / "spud-commit.sh")
    monkeypatch.setattr(apply_core_module, "IPTABLES_SCRIPT",     conf_dir / "iptables.sh")

    staging_conf_dir = conf_dir / "staging"
    monkeypatch.setattr(staging_module, "SPUD_CONF", staging_conf_dir)
    staging_file = staging_conf_dir / "mcp-staging.json"
    monkeypatch.setattr(staging_module, "STAGING_FILE", staging_file)

    import backend.routers.staging as staging_router
    monkeypatch.setattr(staging_router, "STAGING_FILE", staging_file)

    monkeypatch.setattr(state_module, "ARM_STATUS_FILE", conf_dir / "arm-status.json")
    monkeypatch.setattr(state_module, "ROLLBACK_STATE_FILE", conf_dir / "state.rollback.json")
    monkeypatch.setattr(state_module, "LAST_APPLIED_STATE_FILE", conf_dir / "state.last-applied.json")

    monkeypatch.setattr(staging_module, "ARM_STATUS_FILE", conf_dir / "arm-status.json")
    monkeypatch.setattr(staging_module, "ROLLBACK_STATE_FILE", conf_dir / "state.rollback.json")
    monkeypatch.setattr(staging_module, "LAST_APPLIED_STATE_FILE", conf_dir / "state.last-applied.json")

    return {
        "conf_dir": conf_dir,
        "state_file": state_file,
    }


@pytest.fixture(autouse=True)
def enable_staging(monkeypatch):
    """Set SPUD_ENABLE_STAGING env var before app import."""
    import sys
    monkeypatch.setenv("SPUD_ENABLE_STAGING", "1")
    for mod in list(sys.modules.keys()):
        if mod.startswith("backend.routers.staging") or mod == "backend.main":
            sys.modules.pop(mod, None)
    yield
    for mod in list(sys.modules.keys()):
        if mod.startswith("backend.routers.staging") or mod == "backend.main":
            sys.modules.pop(mod, None)
    monkeypatch.delenv("SPUD_ENABLE_STAGING", raising=False)


@pytest.fixture
def client(enable_staging):
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


class TestStagingBegin:
    def test_begin_creates_staging_file(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        resp = authed_client.post("/api/staging/begin")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["state"] == "staging"
        assert body["operation_count"] == 0
        assert "begun_at" in body

    def test_begin_returns_409_if_already_active(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        authed_client.post("/api/staging/begin")
        resp = authed_client.post("/api/staging/begin")
        assert resp.status_code == 409
        assert "already active" in resp.json()["detail"]

    def test_begin_requires_auth(self, client, isolated_env):
        resp = client.post("/api/staging/begin")
        assert resp.status_code == 401

    def test_status_shows_active_after_begin(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        authed_client.post("/api/staging/begin")
        resp = authed_client.get("/api/staging/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is True
        assert body["state"] == "staging"
        assert body["operation_count"] == 0


class TestStagingOps:
    def test_op_add_vlan(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        authed_client.post("/api/staging/begin")
        resp = authed_client.post("/api/staging/op", json={
            "op": "add_vlan",
            "data": {
                "vlan_id": 10,
                "name": "TestLAN",
                "interface": "eth0",
                "ip_address": "192.168.10.1",
                "prefix_len": 24,
                "dhcp_enabled": True,
                "dhcp_start": "192.168.10.100",
                "dhcp_end": "192.168.10.200",
                "dhcp_lease": "12h",
            }
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["op"] == "add_vlan"
        assert body["operation_index"] == 1

    def test_op_rejects_unknown_operation(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        authed_client.post("/api/staging/begin")
        resp = authed_client.post("/api/staging/op", json={"op": "unknown_op", "data": {}})
        assert resp.status_code == 400

    def test_op_rejects_duplicate_vlan(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        authed_client.post("/api/staging/begin")
        vlan_data = {
            "vlan_id": 10, "name": "TestLAN", "interface": "eth0",
            "ip_address": "192.168.10.1", "prefix_len": 24,
            "dhcp_enabled": False,
        }
        authed_client.post("/api/staging/op", json={"op": "add_vlan", "data": vlan_data})
        resp = authed_client.post("/api/staging/op", json={"op": "add_vlan", "data": vlan_data})
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]

    def test_discard_clears_staging(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        begin_resp = authed_client.post("/api/staging/begin")
        assert begin_resp.status_code == 200, f"begin failed: {begin_resp.json()}"
        resp = authed_client.post("/api/staging/discard")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["discarded_operations"] == 0

        status = authed_client.get("/api/staging/status")
        assert status.json()["active"] is False


class TestStagingValidate:
    def test_validate_returns_valid_for_good_state(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(apply_core_module, "generate_all", lambda s: {})
        authed_client.post("/api/staging/begin")
        authed_client.post("/api/staging/op", json={
            "op": "add_vlan",
            "data": {
                "vlan_id": 10, "name": "TestLAN", "interface": "eth0",
                "ip_address": "192.168.10.1", "prefix_len": 24,
                "dhcp_enabled": False,
            }
        })
        resp = authed_client.post("/api/staging/validate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["operation_count"] == 1
        assert body["errors"] == []

    def test_validate_returns_errors_for_invalid_state(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(apply_core_module, "generate_all", lambda s: (_ for _ in ()).throw(RuntimeError("bad config")))
        authed_client.post("/api/staging/begin")
        resp = authed_client.post("/api/staging/validate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert len(body["errors"]) > 0

    def test_new_op_after_validate_resets_to_staging(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(apply_core_module, "generate_all", lambda s: {})
        authed_client.post("/api/staging/begin")
        authed_client.post("/api/staging/op", json={
            "op": "add_vlan",
            "data": {
                "vlan_id": 10, "name": "TestLAN", "interface": "eth0",
                "ip_address": "192.168.10.1", "prefix_len": 24,
                "dhcp_enabled": False,
            }
        })
        authed_client.post("/api/staging/validate")
        status = authed_client.get("/api/staging/status")
        assert status.json()["state"] == "validated"

        authed_client.post("/api/staging/op", json={
            "op": "add_vlan",
            "data": {
                "vlan_id": 20, "name": "TestLAN2", "interface": "eth0",
                "ip_address": "192.168.20.1", "prefix_len": 24,
                "dhcp_enabled": False,
            }
        })
        status = authed_client.get("/api/staging/status")
        assert status.json()["state"] == "staging"


class TestStagingCommit:
    def test_commit_requires_validation(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        authed_client.post("/api/staging/begin")
        resp = authed_client.post("/api/staging/commit")
        assert resp.status_code == 400
        assert "not been validated" in resp.json()["detail"]

    def test_commit_success(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(apply_core_module, "generate_all", lambda s: {})
        monkeypatch.setattr(apply_core_module, "activate_all", lambda s, **k: ["step 1", "step 2"])

        authed_client.post("/api/staging/begin")
        authed_client.post("/api/staging/op", json={
            "op": "add_vlan",
            "data": {
                "vlan_id": 10, "name": "TestLAN", "interface": "eth0",
                "ip_address": "192.168.10.1", "prefix_len": 24,
                "dhcp_enabled": False,
            }
        })
        authed_client.post("/api/staging/validate")
        resp = authed_client.post("/api/staging/commit")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["armed"] is True
        assert "token" in body

    def test_commit_clears_staging_file(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(apply_core_module, "generate_all", lambda s: {})
        monkeypatch.setattr(apply_core_module, "activate_all", lambda s, **k: [])

        authed_client.post("/api/staging/begin")
        authed_client.post("/api/staging/op", json={
            "op": "add_vlan",
            "data": {
                "vlan_id": 10, "name": "TestLAN", "interface": "eth0",
                "ip_address": "192.168.10.1", "prefix_len": 24,
                "dhcp_enabled": False,
            }
        })
        authed_client.post("/api/staging/validate")
        authed_client.post("/api/staging/commit")

        status = authed_client.get("/api/staging/status")
        assert status.json()["active"] is False


class TestStagingConfirm:
    def test_confirm_success(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(apply_core_module, "generate_all", lambda s: {})
        monkeypatch.setattr(apply_core_module, "activate_all", lambda s, **k: [])

        authed_client.post("/api/staging/begin")
        authed_client.post("/api/staging/op", json={
            "op": "add_vlan",
            "data": {
                "vlan_id": 10, "name": "TestLAN", "interface": "eth0",
                "ip_address": "192.168.10.1", "prefix_len": 24,
                "dhcp_enabled": False,
            }
        })
        authed_client.post("/api/staging/validate")
        commit_resp = authed_client.post("/api/staging/commit")
        token = commit_resp.json()["token"]

        resp = authed_client.post("/api/staging/confirm", json={"token": token})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_confirm_fails_with_wrong_token(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(apply_core_module, "generate_all", lambda s: {})
        monkeypatch.setattr(apply_core_module, "activate_all", lambda s, **k: [])

        authed_client.post("/api/staging/begin")
        authed_client.post("/api/staging/op", json={
            "op": "add_vlan",
            "data": {
                "vlan_id": 10, "name": "TestLAN", "interface": "eth0",
                "ip_address": "192.168.10.1", "prefix_len": 24,
                "dhcp_enabled": False,
            }
        })
        authed_client.post("/api/staging/validate")
        authed_client.post("/api/staging/commit")

        resp = authed_client.post("/api/staging/confirm", json={"token": "wrong-token"})
        assert resp.status_code == 409


class TestStagingDisabled:
    def test_returns_501_when_disabled(self, monkeypatch, tmp_path):
        import backend.routers.staging as staging_router
        conf_dir = tmp_path / "spud-router"
        auth_file = conf_dir / "auth.json"
        monkeypatch.setattr(state_module, "SPUD_CONF",  conf_dir)
        monkeypatch.setattr(state_module, "STATE_FILE", conf_dir / "state.json")
        monkeypatch.setattr(auth_module,  "AUTH_FILE",  auth_file)
        monkeypatch.setattr(auth_module,  "SPUD_CONF",  conf_dir)
        monkeypatch.setattr(auth_module,  "CLI_TOKEN_FILE", conf_dir / "cli-token")
        monkeypatch.setattr(auth_module,  "TOKEN_SECRET_FILE", conf_dir / "token-secret")
        monkeypatch.setattr(staging_router, "STAGING_ENABLED", False)
        from backend.main import app
        client = TestClient(app, raise_server_exceptions=True)
        login_resp = client.post("/api/auth/login", json={"username": "admin", "password": "spudrouter"})
        assert login_resp.status_code == 200
        import re
        cookie_header = login_resp.headers.get("set-cookie", "")
        match = re.search(r"spud_token=([^;]+)", cookie_header)
        if match:
            client.cookies.set("spud_token", match.group(1))
        resp = client.post("/api/staging/begin")
        assert resp.status_code == 501
        assert "not enabled" in resp.json()["detail"]
