"""
Tests for update.py — backup/restore, health-gate, rollback, and the overall
apply_update() orchestration. Every filesystem path update.py touches is
monkeypatched into a tmp_path sandbox; nothing here reads or writes the real
/opt/spud-router, /etc/spud-router, /etc/sudoers.d, or /run/spud-router.
"""
import subprocess

import pytest

import update as update_module


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

    monkeypatch.setattr(update_module, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(update_module, "VERSION_FILE", version_file)
    monkeypatch.setattr(update_module, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(update_module, "RUN_UPDATE_SCRIPT", run_update)
    monkeypatch.setattr(update_module, "RUN_DIR", run_dir)
    monkeypatch.setattr(update_module, "STATUS_FILE", status_file)
    monkeypatch.setattr(update_module, "SUDOERS_FILE", sudoers_file)
    monkeypatch.setattr(update_module, "SPUD_CLI_PATH", spud_cli)
    monkeypatch.setattr(update_module, "SSH_BANNER_PATH", ssh_banner)
    monkeypatch.setattr(update_module, "MOTD_PATH", motd)
    monkeypatch.setattr(update_module, "UPDATE_CONFIG_FILE", update_config)

    return {
        "install_dir": install_dir, "version_file": version_file,
        "run_update": run_update, "spud_cli": spud_cli,
        "ssh_banner": ssh_banner, "motd": motd,
        "backup_dir": backup_dir, "run_dir": run_dir,
        "status_file": status_file, "sudoers_file": sudoers_file,
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
