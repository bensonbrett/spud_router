# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Tests for update.py — backup/restore, health-gate, rollback, and the overall
apply_update() orchestration. Every filesystem path update.py touches is
monkeypatched into a tmp_path sandbox; nothing here reads or writes the real
/opt/spud-router, /etc/spud-router, /etc/sudoers.d, or /run/spud-router.
"""
import json
import subprocess
from pathlib import Path

import pytest

import update as update_module


class _RunRecorder:
    """Records subprocess.run invocations and returns a canned result."""
    def __init__(self, rc=0, stdout="", stderr=""):
        self.calls = []
        self._rc, self._out, self._err = rc, stdout, stderr

    def __call__(self, cmd, *a, **k):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self._rc, self._out, self._err)


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Redirect every path constant update.py touches into tmp_path, with a
    minimal fake 'current install' already in place to back up/restore."""
    install_dir = tmp_path / "opt-spud-router"
    (install_dir / "backend").mkdir(parents=True)
    (install_dir / "backend" / "main.py").write_text("# fake backend v1\n")
    (install_dir / "static").mkdir(parents=True)
    (install_dir / "static" / "index.html").write_text("<html>v1</html>")
    version_file = install_dir / "VERSION"
    version_file.write_text("1.0.0")
    run_update = install_dir / "run-update.sh"
    run_update.write_text("#!/bin/bash\necho old\n")
    spud_commit = install_dir / "spud-commit.sh"
    spud_commit.write_text("#!/bin/bash\necho old commit\n")

    spud_cli   = tmp_path / "usr-local-bin" / "spud-cli"
    ssh_banner = tmp_path / "etc-ssh" / "spud-router-banner"
    motd       = tmp_path / "etc-motd" / "99-spud-router"
    for p, content in (
        (spud_cli, "#!/usr/bin/env python3\nold cli\n"),
        (ssh_banner, "old banner"),
        (motd, "#!/bin/bash\necho old motd\n"),
    ):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    run_dir       = tmp_path / "run-spud-router"
    status_file   = run_dir / "update-status.json"
    backup_dir    = install_dir / ".rollback"
    sudoers_file  = tmp_path / "etc-sudoers.d" / "spud-router"
    sudoers_file.parent.mkdir(parents=True)
    update_config = tmp_path / "etc-spud-router" / "update.json"
    update_config.parent.mkdir(parents=True)
    state_file    = tmp_path / "etc-spud-router" / "state.json"
    cf_unit       = tmp_path / "etc-systemd" / "cloudflared-doh.service"
    cf_unit.parent.mkdir(parents=True)
    cf_bin          = tmp_path / "usr-local-bin" / "cloudflared"
    nebula_unit     = tmp_path / "etc-systemd" / "nebula.service"
    nebula_bin      = tmp_path / "usr-local-bin" / "nebula"
    nebula_cert_bin = tmp_path / "usr-local-bin" / "nebula-cert"
    nebula_conf_dir = tmp_path / "etc-nebula"
    rollback_state_file = tmp_path / "etc-spud-router" / "state.rollback.json"
    arm_status_file     = tmp_path / "etc-spud-router" / "arm-status.json"
    commit_status_file  = run_dir / "commit-status.json"

    tls_dir = tmp_path / "etc-spud-router" / "tls"
    tls_dir.mkdir(parents=True, exist_ok=True)
    tls_cert     = tls_dir / "server.crt"
    tls_key      = tls_dir / "server.key"
    tls_cert_bak = tls_dir / "server.crt.bak"
    tls_key_bak  = tls_dir / "server.key.bak"
    tls_restart_status_file = run_dir / "tls-restart-status.json"

    monkeypatch.setattr(update_module, "TLS_DIR", tls_dir)
    monkeypatch.setattr(update_module, "TLS_CERT", tls_cert)
    monkeypatch.setattr(update_module, "TLS_KEY", tls_key)
    monkeypatch.setattr(update_module, "TLS_CERT_BAK", tls_cert_bak)
    monkeypatch.setattr(update_module, "TLS_KEY_BAK", tls_key_bak)
    monkeypatch.setattr(update_module, "TLS_RESTART_STATUS_FILE", tls_restart_status_file)

    monkeypatch.setattr(update_module, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(update_module, "VERSION_FILE", version_file)
    monkeypatch.setattr(update_module, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(update_module, "RUN_UPDATE_SCRIPT", run_update)
    monkeypatch.setattr(update_module, "SPUD_COMMIT_SCRIPT", spud_commit)
    monkeypatch.setattr(update_module, "RUN_DIR", run_dir)
    monkeypatch.setattr(update_module, "STATUS_FILE", status_file)
    monkeypatch.setattr(update_module, "SUDOERS_FILE", sudoers_file)
    monkeypatch.setattr(update_module, "SPUD_CLI_PATH", spud_cli)
    monkeypatch.setattr(update_module, "SSH_BANNER_PATH", ssh_banner)
    monkeypatch.setattr(update_module, "MOTD_PATH", motd)
    monkeypatch.setattr(update_module, "UPDATE_CONFIG_FILE", update_config)
    monkeypatch.setattr(update_module, "STATE_FILE", state_file)
    monkeypatch.setattr(update_module, "CLOUDFLARED_UNIT", cf_unit)
    monkeypatch.setattr(update_module, "CLOUDFLARED_BIN", cf_bin)
    monkeypatch.setattr(update_module, "NEBULA_UNIT", nebula_unit)
    monkeypatch.setattr(update_module, "NEBULA_BIN", nebula_bin)
    monkeypatch.setattr(update_module, "NEBULA_CERT_BIN", nebula_cert_bin)
    monkeypatch.setattr(update_module, "NEBULA_CONF_DIR", nebula_conf_dir)
    monkeypatch.setattr(update_module, "ROLLBACK_STATE_FILE", rollback_state_file)
    monkeypatch.setattr(update_module, "ARM_STATUS_FILE", arm_status_file)
    monkeypatch.setattr(update_module, "COMMIT_STATUS_FILE", commit_status_file)

    return {
        "install_dir": install_dir, "version_file": version_file,
        "run_update": run_update, "spud_commit": spud_commit, "spud_cli": spud_cli,
        "ssh_banner": ssh_banner, "motd": motd,
        "backup_dir": backup_dir, "run_dir": run_dir,
        "status_file": status_file, "sudoers_file": sudoers_file,
        "state_file": state_file, "cf_unit": cf_unit, "cf_bin": cf_bin,
        "tls_dir": tls_dir, "tls_cert": tls_cert, "tls_key": tls_key,
        "tls_cert_bak": tls_cert_bak, "tls_key_bak": tls_key_bak,
        "tls_restart_status_file": tls_restart_status_file,
        "nebula_unit": nebula_unit, "nebula_bin": nebula_bin,
        "nebula_cert_bin": nebula_cert_bin, "nebula_conf_dir": nebula_conf_dir,
        "rollback_state_file": rollback_state_file, "arm_status_file": arm_status_file,
        "commit_status_file": commit_status_file,
    }


def _ok_run(*a, **k):
    return subprocess.CompletedProcess(a, 0, "", "")


# ── Backup / restore ──────────────────────────────────────────────────────────

class TestBackupRestore:
    def test_backup_then_restore_roundtrip(self, sandbox):
        manifest = update_module.backup_current()
        assert sandbox["backup_dir"].exists()
        assert len(manifest) > 0

        # Simulate a bad install overwriting everything
        sandbox["version_file"].write_text("2.0.0")
        (sandbox["install_dir"] / "backend" / "main.py").write_text("# broken\n")
        sandbox["spud_cli"].write_text("broken cli")

        update_module.restore_backup(manifest)

        assert sandbox["version_file"].read_text() == "1.0.0"
        assert (sandbox["install_dir"] / "backend" / "main.py").read_text() == "# fake backend v1\n"
        assert sandbox["spud_cli"].read_text() == "#!/usr/bin/env python3\nold cli\n"

    def test_backup_skips_missing_files(self, sandbox):
        sandbox["ssh_banner"].unlink()
        manifest = update_module.backup_current()
        assert not any(item["src"] == str(sandbox["ssh_banner"]) for item in manifest)

    def test_backup_overwrites_previous_snapshot(self, sandbox):
        update_module.backup_current()
        (sandbox["backup_dir"] / "stale-marker").write_text("x")
        update_module.backup_current()
        assert not (sandbox["backup_dir"] / "stale-marker").exists()

    def test_prune_backup_removes_dir(self, sandbox):
        update_module.backup_current()
        assert sandbox["backup_dir"].exists()
        update_module.prune_backup()
        assert not sandbox["backup_dir"].exists()


# ── Status file ────────────────────────────────────────────────────────────────

class TestStatusFile:
    def test_read_status_defaults_to_idle_when_missing(self, sandbox):
        assert update_module.read_status()["state"] == "idle"

    def test_write_status_merges_and_persists(self, sandbox):
        update_module.write_status(state="running", phase="backup")
        s = update_module.read_status()
        assert s["state"] == "running"
        assert s["phase"] == "backup"

    def test_log_appends_and_persists(self, sandbox):
        update_module.write_status(log=[])
        update_module.log("line one")
        update_module.log("line two")
        assert update_module.read_status()["log"] == ["line one", "line two"]

    def test_status_file_world_readable(self, sandbox):
        update_module.write_status(state="running")
        mode = sandbox["status_file"].stat().st_mode & 0o777
        assert mode == 0o644


# ── concurrency guard (must not detect itself) ─────────────────────────────────

class TestUpdateGuard:
    """
    run-update.sh launches update.py inside a transient systemd unit named
    UPDATE_UNIT, so the is-active probe would see that unit as active and
    abort every real update. The guard must recognise when *we* are that unit.
    """

    def test_running_inside_unit_detected_from_cgroup(self, monkeypatch):
        monkeypatch.setattr(
            update_module.Path, "read_text",
            lambda self: "0::/system.slice/spud-router-update.service\n",
        )
        assert update_module._running_inside_update_unit() is True

    def test_not_inside_unit_when_cgroup_differs(self, monkeypatch):
        monkeypatch.setattr(
            update_module.Path, "read_text",
            lambda self: "0::/system.slice/spud-router.service\n",
        )
        assert update_module._running_inside_update_unit() is False

    def test_guard_skips_self_when_inside_unit(self, monkeypatch):
        # We ARE the detached unit — the guard must not probe systemctl (which
        # would report the unit active, i.e. us) and abort the run.
        monkeypatch.setattr(update_module, "_running_inside_update_unit", lambda: True)

        def _forbidden(*a, **k):
            raise AssertionError("systemctl must not be probed when we are the unit")

        monkeypatch.setattr(update_module.subprocess, "run", _forbidden)
        assert update_module._update_already_running() is False

    def test_guard_detects_a_separate_running_update(self, monkeypatch):
        monkeypatch.setattr(update_module, "_running_inside_update_unit", lambda: False)

        class _R:
            returncode = 0

        monkeypatch.setattr(update_module.subprocess, "run", lambda *a, **k: _R())
        assert update_module._update_already_running() is True

    def test_guard_clear_when_no_update_active(self, monkeypatch):
        monkeypatch.setattr(update_module, "_running_inside_update_unit", lambda: False)

        class _R:
            returncode = 3

        monkeypatch.setattr(update_module.subprocess, "run", lambda *a, **k: _R())
        assert update_module._update_already_running() is False


# ── install_new / sudoers refresh ──────────────────────────────────────────────

class TestInstallNew:
    def test_refreshes_run_update_script(self, sandbox, tmp_path, monkeypatch):
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        (extract_dir / "run-update.sh").write_text("#!/bin/bash\necho new\n")

        update_module._refresh_privileged_files(extract_dir)

        assert sandbox["run_update"].read_text() == "#!/bin/bash\necho new\n"
        assert oct(sandbox["run_update"].stat().st_mode)[-3:] == "755"

    def test_refreshes_spud_commit_script(self, sandbox, tmp_path, monkeypatch):
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        extract_dir = tmp_path / "extracted"
        (extract_dir / "deploy").mkdir(parents=True)
        (extract_dir / "deploy" / "spud-commit.sh").write_text("#!/bin/bash\necho new commit\n")

        update_module._refresh_privileged_files(extract_dir)

        assert sandbox["spud_commit"].read_text() == "#!/bin/bash\necho new commit\n"
        assert oct(sandbox["spud_commit"].stat().st_mode)[-3:] == "755"

    def test_missing_spud_commit_script_is_skipped_not_fatal(self, sandbox, tmp_path, monkeypatch):
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        (extract_dir / "run-update.sh").write_text("#!/bin/bash\necho new\n")
        # No deploy/spud-commit.sh in this release — must not raise.
        update_module._refresh_privileged_files(extract_dir)
        assert sandbox["spud_commit"].read_text() == "#!/bin/bash\necho old commit\n"

    def test_adds_sudoers_lines_when_missing(self, sandbox, monkeypatch):
        sandbox["sudoers_file"].write_text(
            "# existing rule\nspud-router ALL=(root) NOPASSWD: /bin/true\n"
        )
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)

        update_module._ensure_sudoers_lines()

        content = sandbox["sudoers_file"].read_text()
        assert "# existing rule" in content
        assert "run-update.sh apply" in content
        assert "run-update.sh reboot" in content

    def test_sudoers_idempotent(self, sandbox, monkeypatch):
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        update_module._ensure_sudoers_lines()
        first = sandbox["sudoers_file"].read_text()
        update_module._ensure_sudoers_lines()
        assert sandbox["sudoers_file"].read_text() == first

    def test_sudoers_validation_failure_leaves_file_untouched(self, sandbox, monkeypatch):
        sandbox["sudoers_file"].write_text("original content\n")
        monkeypatch.setattr(
            update_module.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "syntax error"),
        )
        update_module._ensure_sudoers_lines()
        assert sandbox["sudoers_file"].read_text() == "original content\n"

    def test_sudoers_created_when_absent(self, sandbox, monkeypatch):
        assert not sandbox["sudoers_file"].exists()
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        update_module._ensure_sudoers_lines()
        assert "run-update.sh apply" in sandbox["sudoers_file"].read_text()


# ── spud-cli integrity guard ───────────────────────────────────────────────────
# Motivated by a real incident: /usr/local/bin/spud-cli (the 'spud' user's
# login shell) was truncated to 0 bytes on a live device. install.sh/update.py
# copied it with no integrity check, so a bad copy would have been silently
# promoted to a login shell — bricking SSH as that user with a cryptic
# "exec format error" and no hint at the cause.

class TestSpudCliIntegrity:
    def test_valid_spudcli_accepts_real_script(self, sandbox):
        assert update_module._valid_spudcli(sandbox["spud_cli"]) is True

    def test_valid_spudcli_rejects_empty_file(self, tmp_path):
        empty = tmp_path / "empty"
        empty.write_text("")
        assert update_module._valid_spudcli(empty) is False

    def test_valid_spudcli_rejects_missing_shebang(self, tmp_path):
        no_shebang = tmp_path / "no-shebang"
        no_shebang.write_text("not a script")
        assert update_module._valid_spudcli(no_shebang) is False

    def test_valid_spudcli_rejects_missing_file(self, tmp_path):
        assert update_module._valid_spudcli(tmp_path / "does-not-exist") is False

    def test_copy_release_files_rejects_zero_byte_spudcli(self, sandbox, tmp_path):
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        (extract_dir / "spud-cli").write_text("")

        with pytest.raises(RuntimeError, match="spud-cli"):
            update_module._copy_release_files(extract_dir)

    def test_copy_release_files_rejects_no_shebang_spudcli(self, sandbox, tmp_path):
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        (extract_dir / "spud-cli").write_text("not a script")

        with pytest.raises(RuntimeError, match="spud-cli"):
            update_module._copy_release_files(extract_dir)

    def test_copy_release_files_accepts_valid_spudcli(self, sandbox, tmp_path):
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        (extract_dir / "spud-cli").write_text("#!/usr/bin/env python3\nnew cli\n")

        update_module._copy_release_files(extract_dir)  # must not raise

        assert sandbox["spud_cli"].read_text() == "#!/usr/bin/env python3\nnew cli\n"

    def test_apply_update_rolls_back_on_zero_byte_spudcli(self, sandbox, monkeypatch):
        """Full round trip: a bad spud-cli in the release must not brick the
        login shell — apply_update() rolls back and the previously-working
        copy is restored."""
        release = {
            "tag": "v2.0.0", "version": "2.0.0",
            "changelog": "notes", "tarball_url": "https://example.invalid/release.tar.gz",
            "sha256": None,
        }

        def fake_extract(tball, extract_dir):
            extract_dir.mkdir(exist_ok=True)
            (extract_dir / "spud-cli").write_text("")  # the bug this guards against

        update_module._start_status("1.0.0", release["version"])
        monkeypatch.setattr(update_module, "download_file", lambda url, dest: dest.write_bytes(b"fake"))
        monkeypatch.setattr(update_module, "_extract_tarball", fake_extract)
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(update_module, "health_gate", lambda *a, **k: True)

        rc = update_module.apply_update(release)

        assert rc == 1
        assert update_module.read_status()["state"] == "rolledback"
        assert sandbox["spud_cli"].read_text() == "#!/usr/bin/env python3\nold cli\n"


# ── Health gate ────────────────────────────────────────────────────────────────

class TestHealthGate:
    def test_true_when_service_active_and_version_matches(self, monkeypatch):
        monkeypatch.setattr(update_module, "_service_active", lambda: True)
        monkeypatch.setattr(update_module, "_fetch_health", lambda: {"status": "ok", "version": "2.0.0"})
        assert update_module.health_gate("2.0.0", timeout=1, poll_interval=0.01) is True

    def test_false_on_version_mismatch_until_timeout(self, monkeypatch):
        monkeypatch.setattr(update_module, "_service_active", lambda: True)
        monkeypatch.setattr(update_module, "_fetch_health", lambda: {"status": "ok", "version": "1.0.0"})
        assert update_module.health_gate("2.0.0", timeout=0.05, poll_interval=0.01) is False

    def test_false_when_service_inactive(self, monkeypatch):
        monkeypatch.setattr(update_module, "_service_active", lambda: False)
        monkeypatch.setattr(update_module, "_fetch_health", lambda: {"status": "ok", "version": "2.0.0"})
        assert update_module.health_gate("2.0.0", timeout=0.05, poll_interval=0.01) is False

    def test_recovers_after_transient_failure(self, monkeypatch):
        calls = {"n": 0}

        def flaky_health():
            calls["n"] += 1
            return None if calls["n"] < 3 else {"status": "ok", "version": "2.0.0"}

        monkeypatch.setattr(update_module, "_service_active", lambda: True)
        monkeypatch.setattr(update_module, "_fetch_health", flaky_health)
        assert update_module.health_gate("2.0.0", timeout=1, poll_interval=0.01) is True


# ── Rollback ───────────────────────────────────────────────────────────────────

class TestRollback:
    def test_restores_files_and_reports_rolledback(self, sandbox, monkeypatch):
        manifest = update_module.backup_current()
        sandbox["version_file"].write_text("2.0.0")
        (sandbox["install_dir"] / "backend" / "main.py").write_text("# broken\n")

        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(update_module, "health_gate", lambda *a, **k: True)

        assert update_module.rollback(manifest, "1.0.0") is True
        assert sandbox["version_file"].read_text() == "1.0.0"
        assert (sandbox["install_dir"] / "backend" / "main.py").read_text() == "# fake backend v1\n"

        status = update_module.read_status()
        assert status["state"] == "rolledback"
        assert "1.0.0" in status["message"]

    def test_health_gate_failure_reports_failed(self, sandbox, monkeypatch):
        manifest = update_module.backup_current()
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(update_module, "health_gate", lambda *a, **k: False)

        assert update_module.rollback(manifest, "1.0.0") is False
        assert update_module.read_status()["state"] == "failed"


# ── tls_restart ─────────────────────────────────────────────────────────────────

class TestTlsRestart:
    def test_healthy_restart_reports_ok(self, sandbox, monkeypatch):
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(update_module, "health_gate", lambda *a, **k: True)

        assert update_module.tls_restart() == 0
        status = json.loads(sandbox["tls_restart_status_file"].read_text())
        assert status["state"] == "ok"

    def test_unhealthy_restart_rolls_back_from_backup(self, sandbox, monkeypatch):
        sandbox["tls_cert"].write_text("new-cert")
        sandbox["tls_key"].write_text("new-key")
        sandbox["tls_cert_bak"].write_text("old-cert")
        sandbox["tls_key_bak"].write_text("old-key")

        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        # First restart (new cert) fails health; rollback restart succeeds.
        calls = {"n": 0}
        def _health_gate(*a, **k):
            calls["n"] += 1
            return calls["n"] > 1
        monkeypatch.setattr(update_module, "health_gate", _health_gate)

        assert update_module.tls_restart() == 1
        assert sandbox["tls_cert"].read_text() == "old-cert"
        assert sandbox["tls_key"].read_text() == "old-key"
        status = json.loads(sandbox["tls_restart_status_file"].read_text())
        assert status["state"] == "rolledback"

    def test_unhealthy_restart_no_backup_reports_failed(self, sandbox, monkeypatch):
        # No backup files exist — nothing to restore.
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(update_module, "health_gate", lambda *a, **k: False)

        assert update_module.tls_restart() == 1
        status = json.loads(sandbox["tls_restart_status_file"].read_text())
        assert status["state"] == "failed"

    def test_rollback_also_fails_health_reports_failed(self, sandbox, monkeypatch):
        sandbox["tls_cert_bak"].write_text("old-cert")
        sandbox["tls_key_bak"].write_text("old-key")
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(update_module, "health_gate", lambda *a, **k: False)

        assert update_module.tls_restart() == 1
        status = json.loads(sandbox["tls_restart_status_file"].read_text())
        assert status["state"] == "failed"
# ── revert_config (commit-confirm auto-revert) ─────────────────────────────────

class TestRevertConfig:
    def test_no_snapshot_is_a_noop(self, sandbox):
        assert not sandbox["rollback_state_file"].exists()
        assert update_module.revert_config() == 0
        assert not sandbox["commit_status_file"].exists()

    def test_unreadable_snapshot_reports_revert_failed(self, sandbox):
        sandbox["rollback_state_file"].parent.mkdir(parents=True, exist_ok=True)
        sandbox["rollback_state_file"].write_text("not valid json {{{")

        assert update_module.revert_config() == 1
        status = json.loads(sandbox["commit_status_file"].read_text())
        assert status["state"] == "revert_failed"

    def test_successful_revert_restores_state_and_cleans_up(self, sandbox, monkeypatch):
        sandbox["rollback_state_file"].parent.mkdir(parents=True, exist_ok=True)
        old_state = {"router": {"wan_interface": "eth0"}}
        sandbox["rollback_state_file"].write_text(json.dumps(old_state))
        sandbox["arm_status_file"].write_text(json.dumps({"token": "abc"}))
        sandbox["state_file"].write_text(json.dumps({"router": {"wan_interface": "eth99"}}))

        import backend.apply_core as apply_core_module
        monkeypatch.setattr(apply_core_module, "activate_all", lambda state, sudo=True: ["Reverted: OK"])

        assert update_module.revert_config() == 0
        assert json.loads(sandbox["state_file"].read_text()) == old_state
        assert not sandbox["rollback_state_file"].exists()
        assert not sandbox["arm_status_file"].exists()
        status = json.loads(sandbox["commit_status_file"].read_text())
        assert status["state"] == "reverted"

    def test_activation_failure_reports_revert_failed_but_keeps_state_restored(self, sandbox, monkeypatch):
        sandbox["rollback_state_file"].parent.mkdir(parents=True, exist_ok=True)
        old_state = {"router": {"wan_interface": "eth0"}}
        sandbox["rollback_state_file"].write_text(json.dumps(old_state))

        import backend.apply_core as apply_core_module
        def _boom(state, sudo=True):
            raise RuntimeError("iptables script failed")
        monkeypatch.setattr(apply_core_module, "activate_all", _boom)

        assert update_module.revert_config() == 1
        # state.json was already restored before activation was attempted —
        # that write isn't rolled back on activation failure.
        assert json.loads(sandbox["state_file"].read_text()) == old_state
        status = json.loads(sandbox["commit_status_file"].read_text())
        assert status["state"] == "revert_failed"
        assert "iptables script failed" in status["message"]


# ── apply_update orchestration ─────────────────────────────────────────────────

class TestApplyUpdate:
    RELEASE = {
        "tag": "v2.0.0", "version": "2.0.0",
        "changelog": "notes", "tarball_url": "https://example.invalid/release.tar.gz",
        "sha256": None,
    }

    def _start(self):
        update_module._start_status("1.0.0", self.RELEASE["version"])

    def test_success_path(self, sandbox, monkeypatch):
        self._start()
        monkeypatch.setattr(update_module, "download_file", lambda url, dest: dest.write_bytes(b"fake"))
        monkeypatch.setattr(update_module, "_extract_tarball", lambda tball, extract_dir: extract_dir.mkdir(exist_ok=True))
        monkeypatch.setattr(update_module, "install_new", lambda extract_dir: None)
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(update_module, "health_gate", lambda *a, **k: True)

        rc = update_module.apply_update(self.RELEASE)

        assert rc == 0
        assert sandbox["version_file"].read_text() == "2.0.0"
        assert not sandbox["backup_dir"].exists()  # pruned on success
        assert update_module.read_status()["state"] == "success"

    def test_rollback_on_failed_health_gate(self, sandbox, monkeypatch):
        self._start()
        monkeypatch.setattr(update_module, "download_file", lambda url, dest: dest.write_bytes(b"fake"))
        monkeypatch.setattr(update_module, "_extract_tarball", lambda tball, extract_dir: extract_dir.mkdir(exist_ok=True))
        monkeypatch.setattr(update_module, "install_new", lambda extract_dir: None)
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        # The new version (2.0.0) never comes up healthy; the restored old
        # version (1.0.0), once rolled back to, does — so rollback succeeds.
        monkeypatch.setattr(update_module, "health_gate", lambda target_version, **k: target_version == "1.0.0")

        rc = update_module.apply_update(self.RELEASE)

        assert rc == 1
        assert sandbox["version_file"].read_text() == "1.0.0"  # restored
        assert update_module.read_status()["state"] == "rolledback"

    def test_never_stuck_running_on_early_exception(self, sandbox, monkeypatch):
        """An exception before any file changes (e.g. download failure) must
        still leave a terminal status, and rolls back since the backup
        (which happens first) already completed."""
        self._start()
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(update_module, "health_gate", lambda *a, **k: True)

        def boom(url, dest):
            raise RuntimeError("network exploded")
        monkeypatch.setattr(update_module, "download_file", boom)

        rc = update_module.apply_update(self.RELEASE)

        assert rc == 1
        assert update_module.read_status()["state"] != "running"

    def test_exception_mid_install_triggers_rollback(self, sandbox, monkeypatch):
        self._start()
        monkeypatch.setattr(update_module, "download_file", lambda url, dest: dest.write_bytes(b"fake"))
        monkeypatch.setattr(update_module, "_extract_tarball", lambda tball, extract_dir: extract_dir.mkdir(exist_ok=True))
        monkeypatch.setattr(update_module.subprocess, "run", _ok_run)
        monkeypatch.setattr(update_module, "health_gate", lambda *a, **k: True)

        def boom(extract_dir):
            raise RuntimeError("install exploded")
        monkeypatch.setattr(update_module, "install_new", boom)

        rc = update_module.apply_update(self.RELEASE)

        assert rc == 1
        assert sandbox["version_file"].read_text() == "1.0.0"
        assert update_module.read_status()["state"] == "rolledback"

    def test_no_rollback_possible_if_backup_itself_fails(self, sandbox, monkeypatch):
        """If backup_current() never completes, there's nothing to roll back
        to — status must be 'failed', not 'rolledback'."""
        self._start()

        def boom():
            raise OSError("disk full")
        monkeypatch.setattr(update_module, "backup_current", boom)

        rc = update_module.apply_update(self.RELEASE)

        assert rc == 1
        assert update_module.read_status()["state"] == "failed"


# ── system-dependency provisioning (OTA parity with install.sh) ────────────────

def _make_release(tmp_path, files: dict):
    """Build a fake extracted release dir with a deploy/ subtree."""
    extract = tmp_path / "extract"
    (extract / "deploy").mkdir(parents=True)
    for name, content in files.items():
        (extract / "deploy" / name).write_text(content)
    return extract


class TestProvisionSystem:
    def test_sudoers_written_from_deploy_when_valid(self, sandbox, tmp_path, monkeypatch):
        extract = _make_release(tmp_path, {
            "sudoers": "Defaults:spud-router !requiretty\n"
                       "spud-router ALL=(root) NOPASSWD: /bin/true\n",
        })
        rec = _RunRecorder(rc=0)  # visudo -c passes
        monkeypatch.setattr(update_module.subprocess, "run", rec)
        update_module._provision_sudoers(extract)
        assert "NOPASSWD: /bin/true" in sandbox["sudoers_file"].read_text()
        assert any("visudo" in c for c in rec.calls)

    def test_sudoers_left_unchanged_when_visudo_fails(self, sandbox, tmp_path, monkeypatch):
        sandbox["sudoers_file"].write_text("# EXISTING GOOD POLICY\n")
        extract = _make_release(tmp_path, {"sudoers": "this is not valid sudoers\n"})
        rec = _RunRecorder(rc=1, stderr="parse error")  # visudo -c fails
        monkeypatch.setattr(update_module.subprocess, "run", rec)
        update_module._provision_sudoers(extract)
        assert sandbox["sudoers_file"].read_text() == "# EXISTING GOOD POLICY\n"

    def test_sudoers_falls_back_to_wrapper_grant_when_deploy_absent(self, sandbox, tmp_path, monkeypatch):
        extract = tmp_path / "extract"       # no deploy/sudoers
        extract.mkdir()
        rec = _RunRecorder(rc=0)
        monkeypatch.setattr(update_module.subprocess, "run", rec)
        update_module._provision_sudoers(extract)
        assert update_module.SUDOERS_MARKER in sandbox["sudoers_file"].read_text()

    def test_systemd_unit_installed_when_changed(self, sandbox, tmp_path, monkeypatch):
        extract = _make_release(tmp_path, {"cloudflared-doh.service": "[Unit]\nDescription=x\n"})
        rec = _RunRecorder(rc=0)
        monkeypatch.setattr(update_module.subprocess, "run", rec)
        update_module._provision_systemd_units(extract)
        assert sandbox["cf_unit"].read_text() == "[Unit]\nDescription=x\n"
        assert any("daemon-reload" in c for c in rec.calls)

    def test_systemd_unit_skipped_when_identical(self, sandbox, tmp_path, monkeypatch):
        sandbox["cf_unit"].write_text("[Unit]\nDescription=same\n")
        extract = _make_release(tmp_path, {"cloudflared-doh.service": "[Unit]\nDescription=same\n"})
        rec = _RunRecorder(rc=0)
        monkeypatch.setattr(update_module.subprocess, "run", rec)
        update_module._provision_systemd_units(extract)
        assert not any("daemon-reload" in c for c in rec.calls)

    def test_packages_installed_ignoring_comments(self, sandbox, tmp_path, monkeypatch):
        extract = _make_release(tmp_path, {"packages": "# a comment\n\nsnmpd\nrsyslog\n"})
        sandbox["state_file"].parent.mkdir(parents=True, exist_ok=True)
        sandbox["state_file"].write_text(json.dumps({"snmp": {"enabled": False}}))
        rec = _RunRecorder(rc=0)
        monkeypatch.setattr(update_module.subprocess, "run", rec)
        update_module._provision_packages(extract)
        apt = [c for c in rec.calls if "apt-get" in c][0]
        assert "snmpd" in apt and "rsyslog" in apt
        assert not any(tok.startswith("#") for tok in apt)

    def test_packages_best_effort_on_failure(self, sandbox, tmp_path, monkeypatch):
        extract = _make_release(tmp_path, {"packages": "snmpd\n"})

        def _boom(*a, **k):
            raise OSError("apt not found")

        monkeypatch.setattr(update_module.subprocess, "run", _boom)
        # Must not raise — best-effort, logged only.
        update_module._provision_packages(extract)

    def test_snmpd_disabled_when_optout(self, sandbox, tmp_path, monkeypatch):
        sandbox["state_file"].parent.mkdir(parents=True, exist_ok=True)
        sandbox["state_file"].write_text(json.dumps({"snmp": {"enabled": False}}))
        rec = _RunRecorder(rc=0)
        monkeypatch.setattr(update_module.subprocess, "run", rec)
        update_module._reconcile_optin_services()
        assert any("disable" in c and "snmpd" in c for c in rec.calls)

    def test_snmpd_untouched_when_enabled(self, sandbox, tmp_path, monkeypatch):
        sandbox["state_file"].parent.mkdir(parents=True, exist_ok=True)
        sandbox["state_file"].write_text(json.dumps({"snmp": {"enabled": True}}))
        rec = _RunRecorder(rc=0)
        monkeypatch.setattr(update_module.subprocess, "run", rec)
        update_module._reconcile_optin_services()
        assert not any("snmpd" in c for c in rec.calls)

    def test_cloudflared_binary_downloaded_when_missing(self, sandbox, monkeypatch):
        rec = _RunRecorder(rc=0)
        monkeypatch.setattr(update_module.subprocess, "run", rec)
        update_module._provision_cloudflared_binary()
        assert any("curl" in c for c in rec.calls)

    def test_cloudflared_binary_skipped_when_present(self, sandbox, monkeypatch):
        sandbox["cf_bin"].parent.mkdir(parents=True, exist_ok=True)
        sandbox["cf_bin"].write_text("#!/bin/sh\n")
        rec = _RunRecorder(rc=0)
        monkeypatch.setattr(update_module.subprocess, "run", rec)
        update_module._provision_cloudflared_binary()
        assert rec.calls == []

    def test_nebula_unit_installed_when_changed(self, sandbox, tmp_path, monkeypatch):
        extract = _make_release(tmp_path, {"nebula.service": "[Unit]\nDescription=nebula\n"})
        rec = _RunRecorder(rc=0)
        monkeypatch.setattr(update_module.subprocess, "run", rec)
        update_module._provision_systemd_units(extract)
        assert sandbox["nebula_unit"].read_text() == "[Unit]\nDescription=nebula\n"
        assert any("daemon-reload" in c for c in rec.calls)

    def test_both_units_installed_together(self, sandbox, tmp_path, monkeypatch):
        extract = _make_release(tmp_path, {
            "cloudflared-doh.service": "[Unit]\nDescription=cf\n",
            "nebula.service": "[Unit]\nDescription=nebula\n",
        })
        rec = _RunRecorder(rc=0)
        monkeypatch.setattr(update_module.subprocess, "run", rec)
        update_module._provision_systemd_units(extract)
        assert sandbox["cf_unit"].read_text() == "[Unit]\nDescription=cf\n"
        assert sandbox["nebula_unit"].read_text() == "[Unit]\nDescription=nebula\n"
        assert len([c for c in rec.calls if "daemon-reload" in c]) == 1

    def test_nebula_binaries_downloaded_and_extracted_when_missing(self, sandbox, monkeypatch):
        import tarfile

        def _fake_run(cmd, *a, **k):
            if cmd[0] == "curl":
                out_path = Path(cmd[cmd.index("-o") + 1])
                src_dir = out_path.parent
                nebula_bin = src_dir / "nebula"
                nebula_cert_bin = src_dir / "nebula-cert"
                nebula_bin.write_text("#!/bin/sh\necho nebula\n")
                nebula_cert_bin.write_text("#!/bin/sh\necho nebula-cert\n")
                with tarfile.open(out_path, "w:gz") as tf:
                    tf.add(nebula_bin, arcname="nebula")
                    tf.add(nebula_cert_bin, arcname="nebula-cert")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[0] == "tar":
                dest = Path(cmd[cmd.index("-C") + 1])
                with tarfile.open(cmd[2]) as tf:
                    tf.extractall(dest)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(update_module.subprocess, "run", _fake_run)
        update_module._provision_nebula_binaries()
        assert sandbox["nebula_bin"].exists()
        assert sandbox["nebula_cert_bin"].exists()
        assert sandbox["nebula_conf_dir"].is_dir()

    def test_nebula_binaries_skipped_when_present(self, sandbox, monkeypatch):
        sandbox["nebula_bin"].parent.mkdir(parents=True, exist_ok=True)
        sandbox["nebula_bin"].write_text("#!/bin/sh\n")
        sandbox["nebula_cert_bin"].write_text("#!/bin/sh\n")
        rec = _RunRecorder(rc=0)
        monkeypatch.setattr(update_module.subprocess, "run", rec)
        update_module._provision_nebula_binaries()
        assert rec.calls == []

    def test_nebula_binaries_best_effort_on_download_failure(self, sandbox, monkeypatch):
        def _boom(*a, **k):
            raise OSError("network unreachable")
        monkeypatch.setattr(update_module.subprocess, "run", _boom)
        # Must not raise — best-effort, logged only.
        update_module._provision_nebula_binaries()
        assert not sandbox["nebula_bin"].exists()
