# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
API tests for the update/reboot/health endpoints — /api/update/apply,
/api/update/status, /api/health, /api/system/reboot.

Separate from test_api.py because these routes pull their path constants
from backend.update via `from ..update import X`, which binds new names into
backend.routers.update / backend.routers.system's own namespaces — those
namespaced copies (not backend.update's) are what must be monkeypatched,
same pattern as auth.py's TOKEN_SECRET_FILE in test_api.py.
"""
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

import backend.state as state_module
import backend.auth as auth_module
import backend.routers.update as update_router
import backend.routers.system as system_router


def _ok_run(*a, **k):
    return subprocess.CompletedProcess(a, 0, "", "")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Redirect state/auth and update/system path constants to tmp_path."""
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

    version_file      = tmp_path / "VERSION"
    version_file.write_text("1.0.0")
    status_file       = tmp_path / "run-spud-router" / "update-status.json"
    run_update_script = tmp_path / "run-update.sh"
    update_config     = tmp_path / "update.json"

    monkeypatch.setattr(update_router, "VERSION_FILE", version_file)
    monkeypatch.setattr(update_router, "STATUS_FILE", status_file)
    monkeypatch.setattr(update_router, "RUN_UPDATE_SCRIPT", run_update_script)
    monkeypatch.setattr(update_router, "UPDATE_CONFIG_FILE", update_config)

    monkeypatch.setattr(system_router, "VERSION_FILE", version_file)
    monkeypatch.setattr(system_router, "RUN_UPDATE_SCRIPT", run_update_script)

    return {
        "version_file": version_file, "status_file": status_file,
        "run_update_script": run_update_script,
    }


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


class TestHealth:
    def test_unauthenticated(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "version": "1.0.0"}

    def test_does_not_require_a_token_unlike_other_endpoints(self, client):
        assert client.get("/api/state").status_code == 401
        assert client.get("/api/health").status_code == 200

    def test_reports_missing_version_file_as_unknown(self, client, isolated_env):
        isolated_env["version_file"].unlink()
        assert client.get("/api/health").json()["version"] == "unknown"


class TestUpdateStatus:
    def test_idle_when_no_status_file(self, authed_client):
        resp = authed_client.get("/api/update/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "idle"
        assert data["installed_version"] == "1.0.0"
        assert "service_active" in data

    def test_reflects_status_file_contents(self, authed_client, isolated_env):
        isolated_env["status_file"].parent.mkdir(parents=True, exist_ok=True)
        isolated_env["status_file"].write_text(json.dumps({
            "state": "running", "phase": "install", "percent": 55, "log": ["a", "b"],
        }))
        data = authed_client.get("/api/update/status").json()
        assert data["state"] == "running"
        assert data["phase"] == "install"
        assert data["log"] == ["a", "b"]

    def test_requires_auth(self, client):
        assert client.get("/api/update/status").status_code == 401


class TestApplyEndpoint:
    def test_starts_update(self, authed_client, monkeypatch):
        # subprocess.run is patched process-wide, so a blanket "always
        # succeed" stub would also make the internal "is the update unit
        # already active?" check look active. Bypass that check directly
        # instead so this test targets only the wrapper-invocation path.
        monkeypatch.setattr(update_router, "_update_running", lambda: False)
        monkeypatch.setattr(update_router.subprocess, "run", _ok_run)
        resp = authed_client.post("/api/update/apply")
        assert resp.status_code == 200
        assert resp.json() == {"started": True}

    def test_rejects_when_already_running(self, authed_client, isolated_env):
        isolated_env["status_file"].parent.mkdir(parents=True, exist_ok=True)
        isolated_env["status_file"].write_text(json.dumps({"state": "running"}))
        resp = authed_client.post("/api/update/apply")
        assert resp.status_code == 409

    def test_500_when_wrapper_invocation_fails(self, authed_client, monkeypatch):
        monkeypatch.setattr(
            update_router.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "permission denied"),
        )
        resp = authed_client.post("/api/update/apply")
        assert resp.status_code == 500

    def test_requires_auth(self, client):
        assert client.post("/api/update/apply").status_code == 401


class TestRebootEndpoint:
    def test_requires_auth(self, client):
        assert client.post("/api/system/reboot").status_code == 401

    def test_invokes_wrapper_with_reboot_arg(self, authed_client, monkeypatch):
        calls = {}

        def fake_run(cmd, **kwargs):
            calls["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")
        monkeypatch.setattr(system_router.subprocess, "run", fake_run)

        resp = authed_client.post("/api/system/reboot")
        assert resp.status_code == 200
        assert resp.json() == {"rebooting": True}
        assert calls["cmd"][0] == "sudo"
        assert calls["cmd"][-1] == "reboot"

    def test_500_when_wrapper_invocation_fails(self, authed_client, monkeypatch):
        monkeypatch.setattr(
            system_router.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "boom"),
        )
        resp = authed_client.post("/api/system/reboot")
        assert resp.status_code == 500
