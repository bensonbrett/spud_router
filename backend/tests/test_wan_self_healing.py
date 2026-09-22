# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
import json
import re

import pytest
from fastapi.testclient import TestClient

import backend.auth as auth_module
import backend.state as state_module
import backend.wan_watchdog as watchdog


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    conf = tmp_path / "conf"
    monkeypatch.setattr(state_module, "SPUD_CONF", conf)
    monkeypatch.setattr(state_module, "STATE_FILE", conf / "state.json")
    monkeypatch.setattr(auth_module, "SPUD_CONF", conf)
    monkeypatch.setattr(auth_module, "AUTH_FILE", conf / "auth.json")
    monkeypatch.setattr(auth_module, "CLI_TOKEN_FILE", conf / "cli-token")
    monkeypatch.setattr(auth_module, "TOKEN_SECRET_FILE", conf / "token-secret")
    monkeypatch.setattr(auth_module, "_revoked", set())


@pytest.fixture
def authed_client():
    from backend.main import app
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"username": "admin", "password": "spudrouter"})
    assert response.status_code == 200
    token = re.search(r"spud_token=([^;]+)", response.headers["set-cookie"]).group(1)
    client.cookies.set("spud_token", token)
    return client


def test_policy_defaults_and_validation(authed_client):
    assert authed_client.get("/api/wan-self-healing").json()["enabled"] is False
    assert authed_client.put("/api/wan-self-healing", json={"failure_minutes": 1}).status_code == 422
    assert authed_client.put("/api/wan-self-healing", json={"probe_hosts": ["not-an-ip"]}).status_code == 422


def test_policy_round_trip(authed_client):
    body = {"enabled": True, "failure_minutes": 60, "reboot_enabled": True,
            "reboot_cooldown_minutes": 720, "probe_hosts": ["1.1.1.1", "8.8.8.8"]}
    assert authed_client.put("/api/wan-self-healing", json=body).status_code == 200
    assert authed_client.get("/api/wan-self-healing").json() == body


def test_gateway_failure_becomes_unhealthy_and_never_reboots_before_threshold(tmp_path, monkeypatch):
    state_file, status_file = tmp_path / "state.json", tmp_path / "status.json"
    state_file.write_text(json.dumps({"wan_self_healing": {
        "enabled": True, "failure_minutes": 60, "reboot_enabled": True,
        "reboot_cooldown_minutes": 720, "probe_hosts": ["1.1.1.1"],
    }}))
    monkeypatch.setattr(watchdog, "STATE_FILE", state_file)
    monkeypatch.setattr(watchdog, "STATUS_FILE", status_file)
    monkeypatch.setattr(watchdog, "_gateway", lambda: ("192.0.2.1", "eth0"))
    monkeypatch.setattr(watchdog, "_run", lambda *args: False)
    monkeypatch.setattr(watchdog.time, "time", lambda: 1_000)
    watchdog.main()
    result = json.loads(status_file.read_text())
    assert result["state"] == "unhealthy"
    assert result["gateway_ok"] is False
    assert "waiting" in result["message"]


def test_sustained_gateway_failure_renews_before_reboot(tmp_path, monkeypatch):
    state_file, status_file = tmp_path / "state.json", tmp_path / "status.json"
    state_file.write_text(json.dumps({"wan_self_healing": {
        "enabled": True, "failure_minutes": 60, "reboot_enabled": False,
        "reboot_cooldown_minutes": 720, "probe_hosts": ["1.1.1.1"],
    }}))
    status_file.write_text(json.dumps({"failure_started_at": 1, "history": []}))
    calls = []
    monkeypatch.setattr(watchdog, "STATE_FILE", state_file)
    monkeypatch.setattr(watchdog, "STATUS_FILE", status_file)
    monkeypatch.setattr(watchdog, "_gateway", lambda: ("192.0.2.1", "eth0"))
    monkeypatch.setattr(watchdog, "_run", lambda *args: calls.append(args) or False)
    monkeypatch.setattr(watchdog.time, "time", lambda: 4_000)
    watchdog.main()
    assert (watchdog.NETWORKCTL, "renew", "eth0") in calls
    assert json.loads(status_file.read_text())["state"] == "unhealthy"
