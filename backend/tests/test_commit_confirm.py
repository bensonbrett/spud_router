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
import backend.update as update_module


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

    # update.py defines its own copies of these path constants (it's the
    # fastapi-free module the detached revert runs under — see its own
    # docstring) — each must be patched separately so revert_config() in
    # these tests reads/writes the same files config_module just wrote,
    # exactly like production's update.py --revert does against the real
    # /etc/spud-router/* paths.
    monkeypatch.setattr(update_module, "STATE_FILE",          state_file)
    monkeypatch.setattr(update_module, "ROLLBACK_STATE_FILE", conf_dir / "state.rollback.json")
    monkeypatch.setattr(update_module, "ARM_STATUS_FILE",     conf_dir / "arm-status.json")
    monkeypatch.setattr(update_module, "RUN_DIR",     tmp_path / "run-spud-router")
    monkeypatch.setattr(update_module, "STATUS_FILE", tmp_path / "run-spud-router" / "update-status.json")
    monkeypatch.setattr(update_module, "COMMIT_STATUS_FILE", tmp_path / "run-spud-router" / "commit-status.json")

    return {
        "rollback_state_file":     conf_dir / "state.rollback.json",
        "arm_status_file":         conf_dir / "arm-status.json",
        "last_applied_state_file": conf_dir / "state.last-applied.json",
        "state_file":              state_file,
    }


def _seed_baseline(authed_client, monkeypatch):
    """
    Establish a confirmed baseline (LAST_APPLIED_STATE_FILE) via one
    apply+confirm cycle, so a *subsequent* apply in the test body exercises
    the normal armed path rather than the "first-ever apply" special case.
    """
    monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
    resp = authed_client.post("/api/apply", json={"dry_run": False})
    assert resp.json()["armed"] is False  # the seed apply itself is the first-ever one
    return resp


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


class TestFirstApplyIsUnarmed:
    """
    An apply with no prior confirmed baseline has nothing safe to revert
    to — arming it would just make a "revert" replay this same apply, so
    it must not arm. Its own state becomes the baseline immediately so
    the *next* apply has something real to roll back to.
    """
    def test_first_apply_is_not_armed(self, authed_client, monkeypatch):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        resp = authed_client.post("/api/apply", json={"dry_run": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body["armed"] is False
        assert any("not armed" in s for s in body["steps"])

    def test_first_apply_does_not_snapshot_a_rollback_target(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        authed_client.post("/api/apply", json={"dry_run": False})
        assert not isolated_env["rollback_state_file"].exists()

    def test_first_apply_establishes_the_baseline(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        authed_client.post("/api/apply", json={"dry_run": False})
        assert isolated_env["last_applied_state_file"].exists()
        baseline = json.loads(isolated_env["last_applied_state_file"].read_text())
        assert "router" in baseline

    def test_second_apply_is_armed(self, authed_client, monkeypatch):
        _seed_baseline(authed_client, monkeypatch)
        resp = authed_client.post("/api/apply", json={"dry_run": False})
        assert resp.json()["armed"] is True


class TestApplyArms:
    def test_successful_apply_returns_armed_token(self, authed_client, monkeypatch):
        _seed_baseline(authed_client, monkeypatch)
        resp = authed_client.post("/api/apply", json={"dry_run": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body["armed"] is True
        assert body["token"]
        assert body["window_seconds"] > 0

    def test_arm_invokes_spud_commit_with_window(self, authed_client, monkeypatch):
        _seed_baseline(authed_client, monkeypatch)
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

    def test_snapshot_captures_the_previous_state_not_the_new_one(self, authed_client, monkeypatch, isolated_env):
        """
        The regression this whole fix is about: the rollback snapshot must
        be what was live *before* this apply, not the state being applied
        right now — otherwise a revert just re-applies the possibly-broken
        change instead of restoring the last known-good config.
        """
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        state_module.save_state({**state_module.empty_state(), "router": {"hostname": "state-A"}})
        authed_client.post("/api/apply", json={"dry_run": False})  # first apply: establishes A as baseline, unarmed

        state_module.save_state({**state_module.empty_state(), "router": {"hostname": "state-B"}})
        authed_client.post("/api/apply", json={"dry_run": False})  # second apply: B goes live, armed against A

        snapshot = json.loads(isolated_env["rollback_state_file"].read_text())
        assert snapshot["router"]["hostname"] == "state-A"

    def test_dry_run_does_not_arm_or_snapshot(self, authed_client, monkeypatch, isolated_env):
        _seed_baseline(authed_client, monkeypatch)
        resp = authed_client.post("/api/apply", json={"dry_run": True})
        assert resp.status_code == 200
        assert "armed" not in resp.json()
        assert not isolated_env["rollback_state_file"].exists()

    def test_arm_failure_does_not_fail_the_whole_apply(self, authed_client, monkeypatch):
        _seed_baseline(authed_client, monkeypatch)
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

    def test_arm_failure_still_promotes_baseline(self, authed_client, monkeypatch, isolated_env):
        """An apply that ends up unarmed (arming itself failed) has no
        confirm step coming — it must promote itself to the baseline right
        away so the *next* apply's rollback target isn't stuck on stale data."""
        _seed_baseline(authed_client, monkeypatch)

        def _run(cmd, *a, **k):
            if len(cmd) >= 3 and cmd[2] == "arm":
                return subprocess.CompletedProcess(cmd, 1, "", "systemd-run failed")
            return _ok_run()
        monkeypatch.setattr(config_module.subprocess, "run", _run)
        state_module.save_state({**state_module.empty_state(), "router": {"hostname": "unarmed-state"}})
        authed_client.post("/api/apply", json={"dry_run": False})

        baseline = json.loads(isolated_env["last_applied_state_file"].read_text())
        assert baseline["router"]["hostname"] == "unarmed-state"

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
        _seed_baseline(authed_client, monkeypatch)
        apply_resp = authed_client.post("/api/apply", json={"dry_run": False})
        token = apply_resp.json()["token"]

        confirm_resp = authed_client.post("/api/apply/confirm", json={"token": token})
        assert confirm_resp.status_code == 200
        assert confirm_resp.json() == {"ok": True, "confirmed": True}
        assert not isolated_env["arm_status_file"].exists()
        assert not isolated_env["rollback_state_file"].exists()

    def test_confirm_promotes_confirmed_state_to_baseline(self, authed_client, monkeypatch, isolated_env):
        _seed_baseline(authed_client, monkeypatch)
        state_module.save_state({**state_module.empty_state(), "router": {"hostname": "confirmed-state"}})
        apply_resp = authed_client.post("/api/apply", json={"dry_run": False})
        token = apply_resp.json()["token"]
        authed_client.post("/api/apply/confirm", json={"token": token})

        baseline = json.loads(isolated_env["last_applied_state_file"].read_text())
        assert baseline["router"]["hostname"] == "confirmed-state"

    def test_confirm_with_wrong_token_rejected(self, authed_client, monkeypatch):
        _seed_baseline(authed_client, monkeypatch)
        authed_client.post("/api/apply", json={"dry_run": False})

        resp = authed_client.post("/api/apply/confirm", json={"token": "not-the-real-token"})
        assert resp.status_code == 409

    def test_confirm_with_nothing_armed_rejected(self, authed_client):
        resp = authed_client.post("/api/apply/confirm", json={"token": "whatever"})
        assert resp.status_code == 409

    def test_confirm_invokes_spud_commit_confirm(self, authed_client, monkeypatch):
        _seed_baseline(authed_client, monkeypatch)
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
        _seed_baseline(authed_client, monkeypatch)
        apply_resp = authed_client.post("/api/apply", json={"dry_run": False})
        token = apply_resp.json()["token"]

        resp = authed_client.get("/api/apply/armed")
        body = resp.json()
        assert body["armed"] is True
        assert body["token"] == token
        assert body["remaining_seconds"] <= body["window_seconds"]

    def test_not_armed_after_confirm(self, authed_client, monkeypatch):
        _seed_baseline(authed_client, monkeypatch)
        apply_resp = authed_client.post("/api/apply", json={"dry_run": False})
        token = apply_resp.json()["token"]
        authed_client.post("/api/apply/confirm", json={"token": token})

        assert authed_client.get("/api/apply/armed").json() == {"armed": False}

    def test_requires_auth(self, client):
        assert client.get("/api/apply/armed").status_code == 401


class TestRevertRestoresPreviousStateNotTheNewOne:
    """
    The regression this fix closes: edit A, apply, confirm — A is now the
    known-good baseline. Edit B, apply (armed against A) — then simulate
    the confirmation window expiring by invoking update.revert_config()
    directly, exactly as the detached systemd timer does in production.
    The live config and state.json must both end up back at A, never B —
    the whole point of the auto-revert safety net is restoring what was
    live *before* the possibly-connectivity-breaking change, not replaying
    that same change.
    """
    def test_expired_window_restores_prior_state_not_the_new_one(self, authed_client, monkeypatch, isolated_env):
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(apply_core_module.subprocess, "run", _ok_run)

        # edit A -> apply -> confirm
        state_module.save_state({**state_module.empty_state(), "router": {"hostname": "state-A"}})
        resp_a = authed_client.post("/api/apply", json={"dry_run": False})
        assert resp_a.json()["armed"] is False  # first-ever apply
        # First apply establishes A as the baseline directly (nothing to
        # confirm — it was never armed), matching production behavior.

        # edit B -> apply (armed against A) -> let the window expire
        state_module.save_state({**state_module.empty_state(), "router": {"hostname": "state-B"}})
        resp_b = authed_client.post("/api/apply", json={"dry_run": False})
        assert resp_b.json()["armed"] is True
        assert state_module.load_state()["router"]["hostname"] == "state-B"  # B is live right now

        # Simulate the watchdog firing: the confirmation window expired
        # without a confirm, so the detached timer invokes update.py --revert.
        rc = update_module.revert_config()
        assert rc == 0

        # Both the live config (activate_all was called with this state)
        # and the on-disk state.json the app reads from must show A, not B.
        assert state_module.load_state()["router"]["hostname"] == "state-A"
        assert not isolated_env["rollback_state_file"].exists()  # pruned after a successful revert

    def test_confirmed_apply_is_never_reverted_by_a_later_expiry(self, authed_client, monkeypatch, isolated_env):
        """Confirming an apply prunes its rollback snapshot — a stray
        revert_config() call afterward (there's no timer left to fire it,
        but prove the data supports that) is a no-op, not a state clobber."""
        monkeypatch.setattr(config_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(apply_core_module.subprocess, "run", _ok_run)

        state_module.save_state({**state_module.empty_state(), "router": {"hostname": "state-A"}})
        authed_client.post("/api/apply", json={"dry_run": False})

        state_module.save_state({**state_module.empty_state(), "router": {"hostname": "state-B"}})
        resp_b = authed_client.post("/api/apply", json={"dry_run": False})
        token = resp_b.json()["token"]
        authed_client.post("/api/apply/confirm", json={"token": token})

        rc = update_module.revert_config()
        assert rc == 0  # no-op: nothing armed
        assert state_module.load_state()["router"]["hostname"] == "state-B"
