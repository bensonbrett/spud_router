# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
API tests for the commit-confirmed apply / auto-revert endpoints:
POST /api/apply (armed mode), POST /api/apply/confirm, GET /api/apply/armed.

subprocess.run is mocked throughout — these tests never actually invoke
sudo/systemd-run/spud-commit.sh, and assert on the exact commands the
endpoints construct instead.
"""
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

import backend.state as state_module
import backend.auth as auth_module
import backend.apply_core as apply_core_module
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

    monkeypatch.setattr(config_module, "APPLIED_SNAPSHOT_FILE", conf_dir / "applied.json")
    monkeypatch.setattr(config_module, "ROLLBACK_STATE_FILE",   conf_dir / "state.rollback.json")
    monkeypatch.setattr(config_module, "ARM_STATUS_FILE",       conf_dir / "arm-status.json")
    monkeypatch.setattr(config_module, "SPUD_COMMIT_SCRIPT",    tmp_path / "spud-commit.sh")
    monkeypatch.setattr(apply_core_module, "IPTABLES_SCRIPT",   conf_dir / "iptables.sh")

    return {
        "rollback_state_file": conf_dir / "state.rollback.json",
        "arm_status_file":     conf_dir / "arm-status.json",
    }


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


class TestApplyArms:
    def test_successful_apply_returns_armed_token(self, authed_client, monkeypatch):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        resp = authed_client.post("/api/apply", json={"dry_run": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body["armed"] is True
        assert body["token"]
        assert body["window_seconds"] > 0

    def test_arm_invokes_spud_commit_with_window(self, authed_client, monkeypatch):
        calls = []
        def _record(cmd, *a, **k):
            calls.append(cmd)
            return _ok_run()
        monkeypatch.setattr(config_module.subprocess, "run", _record)

        authed_client.post("/api/apply", json={"dry_run": False})

        arm_calls = [c for c in calls if len(c) >= 3 and c[2] == "arm"]
        assert len(arm_calls) == 1
        assert arm_calls[0][0] == "sudo"
        assert arm_calls[0][1].endswith("spud-commit.sh")
        assert arm_calls[0][3] == str(config_module.ARM_WINDOW_SECONDS)

    def test_snapshot_written_before_activation(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        authed_client.post("/api/apply", json={"dry_run": False})
        assert isolated_env["rollback_state_file"].exists()
        snapshot = json.loads(isolated_env["rollback_state_file"].read_text())
        assert "router" in snapshot

    def test_dry_run_does_not_arm_or_snapshot(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        resp = authed_client.post("/api/apply", json={"dry_run": True})
        assert resp.status_code == 200
        assert "armed" not in resp.json()
        assert not isolated_env["rollback_state_file"].exists()

    def test_arm_failure_does_not_fail_the_whole_apply(self, authed_client, monkeypatch):
        def _run(cmd, *a, **k):
            if len(cmd) >= 3 and cmd[2] == "arm":
                return subprocess.CompletedProcess(cmd, 1, "", "systemd-run failed")
            return _ok_run()
        monkeypatch.setattr(config_module.subprocess, "run", _run)

        resp = authed_client.post("/api/apply", json={"dry_run": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["armed"] is False
        assert any("Could not arm" in s for s in body["steps"])

    def test_apply_failure_returns_500_and_does_not_arm(self, authed_client, monkeypatch):
        def _fail(cmd, *a, **k):
            if k.get("check"):
                raise subprocess.CalledProcessError(1, cmd, stderr="tee: permission denied")
            return subprocess.CompletedProcess(cmd, 1, "", "tee: permission denied")
        monkeypatch.setattr(config_module.subprocess, "run", _fail)

        resp = authed_client.post("/api/apply", json={"dry_run": False})
        assert resp.status_code == 500


class TestApplyConfirm:
    def test_confirm_with_correct_token_cancels_and_cleans_up(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        apply_resp = authed_client.post("/api/apply", json={"dry_run": False})
        token = apply_resp.json()["token"]

        confirm_resp = authed_client.post("/api/apply/confirm", json={"token": token})
        assert confirm_resp.status_code == 200
        assert confirm_resp.json() == {"ok": True, "confirmed": True}
        assert not isolated_env["arm_status_file"].exists()
        assert not isolated_env["rollback_state_file"].exists()

    def test_confirm_with_wrong_token_rejected(self, authed_client, monkeypatch):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        authed_client.post("/api/apply", json={"dry_run": False})

        resp = authed_client.post("/api/apply/confirm", json={"token": "not-the-real-token"})
        assert resp.status_code == 409

    def test_confirm_with_nothing_armed_rejected(self, authed_client):
        resp = authed_client.post("/api/apply/confirm", json={"token": "whatever"})
        assert resp.status_code == 409

    def test_confirm_invokes_spud_commit_confirm(self, authed_client, monkeypatch):
        calls = []
        def _record(cmd, *a, **k):
            calls.append(cmd)
            return _ok_run()
        monkeypatch.setattr(config_module.subprocess, "run", _record)

        apply_resp = authed_client.post("/api/apply", json={"dry_run": False})
        token = apply_resp.json()["token"]
        authed_client.post("/api/apply/confirm", json={"token": token})

        confirm_calls = [c for c in calls if len(c) >= 3 and c[2] == "confirm"]
        assert len(confirm_calls) == 1
        assert confirm_calls[0][0] == "sudo"

    def test_requires_auth(self, client):
        resp = client.post("/api/apply/confirm", json={"token": "x"})
        assert resp.status_code == 401


class TestApplyArmedStatus:
    def test_not_armed_by_default(self, authed_client):
        resp = authed_client.get("/api/apply/armed")
        assert resp.status_code == 200
        assert resp.json() == {"armed": False}

    def test_armed_after_apply(self, authed_client, monkeypatch):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        apply_resp = authed_client.post("/api/apply", json={"dry_run": False})
        token = apply_resp.json()["token"]

        resp = authed_client.get("/api/apply/armed")
        body = resp.json()
        assert body["armed"] is True
        assert body["token"] == token
        assert body["remaining_seconds"] <= body["window_seconds"]

    def test_not_armed_after_confirm(self, authed_client, monkeypatch):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        apply_resp = authed_client.post("/api/apply", json={"dry_run": False})
        token = apply_resp.json()["token"]
        authed_client.post("/api/apply/confirm", json={"token": token})

        assert authed_client.get("/api/apply/armed").json() == {"armed": False}

    def test_requires_auth(self, client):
        assert client.get("/api/apply/armed").status_code == 401
