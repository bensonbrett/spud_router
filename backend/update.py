#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
spud-router updater — standalone update script.

Can be run directly from SSH:
    sudo python3 /opt/spud-router/update.py

Or invoked non-interactively (identical behavior) by the root-owned
run-update.sh wrapper via a detached systemd-run unit, which is how the web
UI / CLI trigger it (backend/routers/update.py -> sudo run-update.sh apply).
Running detached means the update survives the `systemctl restart
spud-router` it performs partway through — it is not a child of the process
it restarts.

Progress (including everything printed here) is mirrored line-by-line into
/run/spud-router/update-status.json so callers can poll it across the
restart window instead of relying on a stdout stream.

Exit codes:
    0 — success
    1 — update failed (auto-rolled-back if a backup existed)
    2 — already up to date
"""
import argparse
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
UPDATE_CONFIG_FILE = Path("/etc/spud-router/update.json")
INSTALL_DIR         = Path("/opt/spud-router")
VERSION_FILE        = INSTALL_DIR / "VERSION"
BACKUP_DIR          = INSTALL_DIR / ".rollback"
RUN_UPDATE_SCRIPT   = INSTALL_DIR / "run-update.sh"

RUN_DIR      = Path("/run/spud-router")
STATUS_FILE  = RUN_DIR / "update-status.json"

SUDOERS_FILE = Path("/etc/sudoers.d/spud-router")
SUDOERS_MARKER = "# spud-router: update/reboot wrapper (managed by update.py)"
SUDOERS_LINES = [
    SUDOERS_MARKER,
    "spud-router ALL=(root) NOPASSWD: /opt/spud-router/run-update.sh apply",
    "spud-router ALL=(root) NOPASSWD: /opt/spud-router/run-update.sh reboot",
]

# Files outside INSTALL_DIR that a release may replace — kept as separate
# constants (rather than inline Paths) so tests can redirect them.
SPUD_CLI_PATH   = Path("/usr/local/bin/spud-cli")
SSH_BANNER_PATH = Path("/etc/ssh/spud-router-banner")
MOTD_PATH       = Path("/etc/update-motd.d/99-spud-router")

# System-dependency provisioning targets (see _provision_system). The updater
# runs as root (systemd-run under run-update.sh), so these are written directly.
STATE_FILE        = Path("/etc/spud-router/state.json")
DNSPROXY_UNIT     = Path("/etc/systemd/system/dnsproxy-doh.service")
DNSPROXY_BIN      = Path("/usr/local/bin/dnsproxy")
DNSPROXY_VERSION  = "v0.82.1"   # pinned — bump deliberately; see PR for #127
NEBULA_UNIT       = Path("/etc/systemd/system/nebula.service")
NEBULA_BIN        = Path("/usr/local/bin/nebula")
NEBULA_CERT_BIN   = Path("/usr/local/bin/nebula-cert")
NEBULA_CONF_DIR   = Path("/etc/nebula")

UPDATE_UNIT  = "spud-router-update"   # transient systemd-run unit name
HEALTH_URL   = "https://127.0.0.1:8080/api/health"

# TLS cert files (see backend/routers/system.py, which writes these — the
# spud-router service owns /etc/spud-router/tls/, this script just restores
# the backup slot on a failed restart).
TLS_DIR      = Path("/etc/spud-router/tls")
TLS_CERT     = TLS_DIR / "server.crt"
TLS_KEY      = TLS_DIR / "server.key"
TLS_CERT_BAK = TLS_DIR / "server.crt.bak"
TLS_KEY_BAK  = TLS_DIR / "server.key.bak"
TLS_RESTART_STATUS_FILE = RUN_DIR / "tls-restart-status.json"
# Commit-confirmed apply / auto-revert (see backend/apply_core.py,
# deploy/spud-commit.sh, backend/routers/config.py's arm/confirm endpoints).
SPUD_COMMIT_SCRIPT  = INSTALL_DIR / "spud-commit.sh"
ROLLBACK_STATE_FILE = Path("/etc/spud-router/state.rollback.json")
ARM_STATUS_FILE     = Path("/etc/spud-router/arm-status.json")
COMMIT_STATUS_FILE  = RUN_DIR / "commit-status.json"

DEFAULT_CONFIG = {
    "github_owner": "bensonbrett",
    "github_repo":  "spud_router",
}


def load_update_config() -> dict:
    if UPDATE_CONFIG_FILE.exists():
        try:
            return json.loads(UPDATE_CONFIG_FILE.read_text())
        except Exception:
            pass
    return DEFAULT_CONFIG


def current_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return "unknown"


# ── Status file (single source of truth for callers polling progress) ────────

def _default_status() -> dict:
    return {
        "state": "idle", "from_version": "", "to_version": "",
        "phase": "", "percent": 0, "log": [], "message": "",
        "started_at": 0, "updated_at": 0,
    }


def read_status() -> dict:
    """Return the current status file contents, or a default 'idle' status."""
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return _default_status()


def write_status(**fields) -> dict:
    """
    Merge fields into the status file and write it atomically (temp file +
    os.replace). The file itself is the source of truth — not an in-memory
    cache — so this is safe across separate process invocations.
    """
    status = read_status()
    status.update(fields)
    status["updated_at"] = time.time()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(status))
    os.replace(tmp, STATUS_FILE)
    try:
        STATUS_FILE.chmod(0o644)  # readable by the spud-router service user
    except OSError:
        pass
    return status


def _start_status(from_version: str, to_version: str = "") -> None:
    """Reset the status file for a fresh run."""
    write_status(
        state="running", from_version=from_version, to_version=to_version,
        phase="check", percent=0, log=[], message="", started_at=time.time(),
    )


def log(msg: str) -> None:
    """Print a progress line and append it to the status file's log."""
    print(msg, flush=True)
    status = read_status()
    write_status(log=status.get("log", []) + [msg])


def _running_inside_update_unit() -> bool:
    """
    True if *this* process is itself the detached updater: run-update.sh
    launches update.py inside a transient systemd unit named UPDATE_UNIT, so
    our own cgroup is that unit. Without this check, the is-active probe in
    _update_already_running() detects ourselves and every update aborts with
    "an update is already running".
    """
    try:
        cgroup = Path("/proc/self/cgroup").read_text()
    except OSError:
        return False
    return f"{UPDATE_UNIT}.service" in cgroup


def _update_already_running() -> bool:
    """True if a *separate* detached updater unit is already active."""
    # We are the detached unit ourselves — don't count that as a collision,
    # or the guard in main() aborts the very run it's meant to protect.
    if _running_inside_update_unit():
        return False
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", UPDATE_UNIT],
        ).returncode == 0
    except Exception:
        return False


# ── GitHub release fetching ───────────────────────────────────────────────────

def fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "spud-router-updater/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def download_file(url: str, dest: Path) -> None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "spud-router-updater/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        total   = int(resp.headers.get("Content-Length", 0))
        written = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
                if total:
                    pct = written * 100 // total
                    log(f"  Downloading… {pct}%")
    log(f"  Downloaded {written // 1024} KB")


def verify_checksum(path: Path, expected_sha256: str) -> bool:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest() == expected_sha256


def get_latest_release(owner: str, repo: str) -> dict:
    """
    Return info about the latest GitHub release.

    Returns:
        {
            "tag":          "v1.2.0",
            "version":      "1.2.0",
            "changelog":    "...",
            "tarball_url":  "https://...",
            "sha256":       "abc123..." | None,
        }
    """
    url      = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    release  = fetch_json(url)
    tag      = release["tag_name"]
    version  = tag.lstrip("v")
    body     = release.get("body", "No release notes.")

    # Find the release tarball asset
    tarball_url = None
    sha256      = None
    for asset in release.get("assets", []):
        name = asset["name"]
        if name.endswith(".tar.gz") and "spud-router" in name:
            tarball_url = asset["browser_download_url"]
        if name.endswith(".sha256"):
            # Download the checksum file
            try:
                req = urllib.request.Request(
                    asset["browser_download_url"],
                    headers={"User-Agent": "spud-router-updater/1.0"},
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    sha256 = r.read().decode().split()[0]
            except Exception:
                pass

    if not tarball_url:
        raise RuntimeError(f"No .tar.gz asset found in release {tag}")

    return {
        "tag":         tag,
        "version":     version,
        "changelog":   body,
        "tarball_url": tarball_url,
        "sha256":      sha256,
    }


# ── Backup / restore ──────────────────────────────────────────────────────────

def _backup_items() -> list[Path]:
    """Everything install_new() may overwrite, computed fresh (not frozen at
    import time) so tests can redirect the path constants above."""
    return [
        INSTALL_DIR / "backend",
        INSTALL_DIR / "static" / "index.html",
        INSTALL_DIR / "static" / "assets",
        VERSION_FILE,
        SPUD_CLI_PATH,
        SSH_BANNER_PATH,
        MOTD_PATH,
        INSTALL_DIR / "update.py",
        RUN_UPDATE_SCRIPT,
    ]


def backup_current() -> list[dict]:
    """
    Copy every file/dir install_new() may overwrite into BACKUP_DIR (freshly
    recreated) so a failed update can be restored exactly. Returns the
    manifest (also written to BACKUP_DIR/manifest.json) that rollback()/
    restore_backup() consume.
    """
    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR)
    BACKUP_DIR.mkdir(parents=True)

    manifest: list[dict] = []
    for i, src in enumerate(_backup_items()):
        if not src.exists():
            continue
        is_dir = src.is_dir()
        dest   = BACKUP_DIR / f"{i:02d}_{src.name}"
        if is_dir:
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        manifest.append({"src": str(src), "backup": str(dest), "is_dir": is_dir})

    (BACKUP_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def restore_backup(manifest: list[dict]) -> None:
    """Restore every item in a backup_current() manifest to its original path."""
    for item in manifest:
        src  = Path(item["backup"])
        dest = Path(item["src"])
        if not src.exists():
            continue
        if item["is_dir"]:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)


def prune_backup() -> None:
    """Remove the rollback snapshot after a confirmed-successful update."""
    shutil.rmtree(BACKUP_DIR, ignore_errors=True)


# ── Install ───────────────────────────────────────────────────────────────────

def _extract_tarball(tball: Path, extract_dir: Path) -> None:
    """Safely extract the release tarball (blocks symlink/device/path-traversal attacks)."""
    with tarfile.open(tball) as tf:
        # Use filter='data' on Python 3.12+ to block symlink/device
        # attacks; fall back to a manual check on older versions.
        if sys.version_info >= (3, 12):
            tf.extractall(extract_dir, filter="data")
        else:
            for member in tf.getmembers():
                mp = Path(member.name)
                if mp.is_absolute() or ".." in mp.parts:
                    raise RuntimeError(f"Unsafe path in tarball: {member.name}")
            tf.extractall(extract_dir)


def _valid_spudcli(path: Path) -> bool:
    """
    Mirrors install.sh's _valid_spudcli(): non-empty and starts with a
    shebang. /usr/local/bin/spud-cli is the 'spud' user's login shell, so a
    truncated/invalid copy silently promoted there bricks SSH as that user
    with a cryptic "exec format error" and no hint at the cause.
    """
    try:
        if path.stat().st_size == 0:
            return False
        with path.open("rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def _copy_release_files(extract_dir: Path) -> None:
    """
    Copy files from the extracted tarball into the install directory.

    Tarball layout:
        install.sh
        backend/
        spud-cli
        ssh-banner
        motd
        update.py
        run-update.sh
        index.html
        assets/          (optional Vite chunks)

    We skip install.sh — it's for fresh installs only.
    """
    file_map = {
        "spud-cli":   SPUD_CLI_PATH,
        "ssh-banner": SSH_BANNER_PATH,
        "motd":       MOTD_PATH,
        "index.html": INSTALL_DIR / "static" / "index.html",
        "update.py":  INSTALL_DIR / "update.py",
    }

    for src_name, dest in file_map.items():
        src = extract_dir / src_name
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            log(f"  ✓ {src_name} → {dest}")
            if src_name == "spud-cli":
                # Only validate right after a fresh copy — this is a guard
                # against *this* write coming out truncated/broken, not a
                # general health check of whatever was already on disk. A
                # bad copy here must not be silently promoted to the 'spud'
                # user's login shell; raising lets apply_update()'s existing
                # rollback restore the previously-working spud-cli.
                dest.chmod(0o755)
                if not _valid_spudcli(dest):
                    raise RuntimeError(
                        f"spud-cli copied to {dest} is empty or not a valid script — "
                        "refusing to leave the 'spud' user's login shell pointed at a broken file"
                    )
        else:
            log(f"  - {src_name} not in release (skipped)")

    # Backend directory
    src_backend  = extract_dir / "backend"
    dest_backend = INSTALL_DIR / "backend"
    if src_backend.exists():
        if dest_backend.exists():
            shutil.rmtree(dest_backend)
        shutil.copytree(src_backend, dest_backend)
        log(f"  ✓ backend/ → {dest_backend}")

    # Assets directory (Vite JS/CSS chunks)
    src_assets  = extract_dir / "assets"
    dest_assets = INSTALL_DIR / "static" / "assets"
    if src_assets.exists():
        if dest_assets.exists():
            shutil.rmtree(dest_assets)
        shutil.copytree(src_assets, dest_assets)
        log(f"  ✓ assets/ → {dest_assets}")

    # Ensure spud-cli is executable
    if SPUD_CLI_PATH.exists():
        SPUD_CLI_PATH.chmod(0o755)

    # Ensure motd is executable
    if MOTD_PATH.exists():
        MOTD_PATH.chmod(0o755)

    # Ensure update.py is executable (supports direct SSH invocation)
    updater = INSTALL_DIR / "update.py"
    if updater.exists():
        updater.chmod(0o755)


def _ensure_sudoers_lines() -> None:
    """
    Idempotently append the run-update.sh sudoers grant if missing, without
    touching any other rule in the file. Validated with `visudo -c` before
    being moved into place — on any validation failure the existing file (if
    any) is left completely untouched, never leaving a broken sudoers file.
    """
    existing = SUDOERS_FILE.read_text() if SUDOERS_FILE.exists() else ""
    if SUDOERS_MARKER in existing:
        return  # already wired up

    addition  = "\n".join(SUDOERS_LINES) + "\n"
    candidate = (existing.rstrip("\n") + "\n\n" + addition) if existing.strip() else addition

    tmp = SUDOERS_FILE.with_suffix(".tmp")
    tmp.write_text(candidate)
    tmp.chmod(0o440)
    check = subprocess.run(["visudo", "-c", "-f", str(tmp)], capture_output=True, text=True)
    if check.returncode != 0:
        tmp.unlink(missing_ok=True)
        log(f"  WARNING: sudoers validation failed — leaving existing file unchanged: {check.stderr.strip()}")
        return
    os.replace(tmp, SUDOERS_FILE)
    SUDOERS_FILE.chmod(0o440)
    log(f"  ✓ sudoers updated ({SUDOERS_FILE})")


def _refresh_privileged_files(extract_dir: Path) -> None:
    """
    Install/refresh the root-owned wrapper scripts (run-update.sh,
    spud-commit.sh) that let the non-root spud-router service invoke a
    fixed set of root actions via sudo. Runs on every update (not just
    fresh installs) so an install that predates one of these — or a
    device the maintainer hasn't re-run install.sh on — picks up the
    wrapper automatically the next time it updates.
    """
    for src_name, dest in (
        ("run-update.sh", RUN_UPDATE_SCRIPT),
        ("deploy/spud-commit.sh", SPUD_COMMIT_SCRIPT),
    ):
        src = extract_dir / src_name
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            dest.chmod(0o755)
            log(f"  ✓ {src_name} → {dest}")
        else:
            log(f"  - {src_name} not in release (skipped)")


def _provision_sudoers(extract_dir: Path) -> None:
    """
    Install the full sudoers policy from the release's deploy/sudoers (the
    single source of truth shared with install.sh). Validated with `visudo -c`
    and atomically replaced — a syntactically invalid file is never left live.
    Older releases ship no deploy/sudoers, so fall back to the append-only
    grant that at least keeps the update/reboot wrapper working.
    """
    src = extract_dir / "deploy" / "sudoers"
    if not src.exists():
        _ensure_sudoers_lines()
        return

    tmp = SUDOERS_FILE.with_suffix(".tmp")
    tmp.write_text(src.read_text())
    tmp.chmod(0o440)
    check = subprocess.run(["visudo", "-c", "-f", str(tmp)], capture_output=True, text=True)
    if check.returncode != 0:
        tmp.unlink(missing_ok=True)
        log(f"  WARNING: sudoers validation failed — leaving existing file unchanged: {check.stderr.strip()}")
        return
    os.replace(tmp, SUDOERS_FILE)
    SUDOERS_FILE.chmod(0o440)
    log(f"  ✓ sudoers policy refreshed ({SUDOERS_FILE})")


def _provision_systemd_units(extract_dir: Path) -> None:
    """
    Install/refresh systemd units that ship with the release (currently the
    dnsproxy-doh proxy used by DoH mode, and the nebula overlay unit).
    Does not change enabled/running state — apply() manages that from
    config; this only makes each unit exist and be current. A single
    daemon-reload covers whichever units actually changed.

    The (name, dest) list is built fresh on every call — rather than
    precomputed at module import time — so tests can monkeypatch
    DNSPROXY_UNIT/NEBULA_UNIT and have it take effect here; a
    module-level list would have captured the original Path objects at
    import time instead.
    """
    units = [
        ("dnsproxy-doh.service", DNSPROXY_UNIT),
        ("nebula.service", NEBULA_UNIT),
    ]
    changed = False
    for name, dest in units:
        src = extract_dir / "deploy" / name
        if not src.exists():
            continue
        try:
            current = dest.read_text() if dest.exists() else ""
            if current == src.read_text():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            dest.chmod(0o644)
            changed = True
            log(f"  ✓ {name} → {dest}")
        except OSError as e:
            log(f"  WARNING: could not install {name}: {e}")
    if changed:
        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True, text=True)


def _provision_staging_env() -> None:
    """
    Enable the staging pipeline for future config changes by dropping in a
    systemd override that sets SPUD_ENABLE_STAGING=1 on the spud-router service.
    Idempotent — skips if the override already exists.
    """
    overrides_dir = Path("/etc/systemd/system/spud-router.service.d")
    override_file = overrides_dir / "staging.conf"
    if override_file.exists():
        return
    overrides_dir.mkdir(parents=True, exist_ok=True)
    try:
        override_file.write_text(
            "[Service]\n"
            "Environment=SPUD_ENABLE_STAGING=1\n"
        )
        override_file.chmod(0o644)
        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True, text=True)
        log("  ✓ SPUD_ENABLE_STAGING=1 set on spud-router service")
    except OSError as e:
        log(f"  WARNING: could not set SPUD_ENABLE_STAGING: {e}")


def _provision_dnsproxy_binary() -> None:
    """
    Download+extract the dnsproxy binary if missing (DoH needs it).
    Best-effort. Unlike cloudflared's single raw binary, dnsproxy ships a
    per-arch tarball (with a versioned filename, hence DNSPROXY_VERSION
    being pinned rather than resolved via a "latest" URL) that extracts into
    a linux-<arch>/ subdirectory alongside a README/LICENSE — mirrors
    _provision_nebula_binaries()'s extract-then-copy-out shape.
    """
    if DNSPROXY_BIN.exists():
        return
    arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(os.uname().machine, "amd64")
    asset = f"dnsproxy-linux-{arch}-{DNSPROXY_VERSION}.tar.gz"
    url = f"https://github.com/AdguardTeam/dnsproxy/releases/download/{DNSPROXY_VERSION}/{asset}"
    try:
        with tempfile.TemporaryDirectory() as d:
            tarball = Path(d) / "dnsproxy.tar.gz"
            subprocess.run(
                ["curl", "-fsSL", url, "-o", str(tarball)],
                check=True, capture_output=True, text=True, timeout=300,
            )
            subprocess.run(
                ["tar", "-xzf", str(tarball), "-C", d],
                check=True, capture_output=True, text=True, timeout=60,
            )
            shutil.copy2(Path(d) / f"linux-{arch}" / "dnsproxy", DNSPROXY_BIN)
        DNSPROXY_BIN.chmod(0o755)
        log(f"  ✓ dnsproxy binary installed ({arch}, {DNSPROXY_VERSION})")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        log(f"  WARNING: dnsproxy download skipped — DoH unavailable until installed ({e})")


def _provision_nebula_binaries() -> None:
    """
    Download the nebula + nebula-cert binaries if missing (join-only #91
    needs both — nebula-cert for import-time credential verification).
    Best-effort, same extract-then-copy-out shape as
    _provision_dnsproxy_binary(), except upstream ships both binaries in one
    per-arch tarball instead of dnsproxy's single-binary one.
    """
    if NEBULA_BIN.exists() and NEBULA_CERT_BIN.exists():
        return
    arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(os.uname().machine, "amd64")
    url = f"https://github.com/slackhq/nebula/releases/latest/download/nebula-linux-{arch}.tar.gz"
    try:
        with tempfile.TemporaryDirectory() as d:
            tarball = Path(d) / "nebula.tar.gz"
            subprocess.run(
                ["curl", "-fsSL", url, "-o", str(tarball)],
                check=True, capture_output=True, text=True, timeout=300,
            )
            subprocess.run(
                ["tar", "-xzf", str(tarball), "-C", d],
                check=True, capture_output=True, text=True, timeout=60,
            )
            shutil.copy2(Path(d) / "nebula", NEBULA_BIN)
            shutil.copy2(Path(d) / "nebula-cert", NEBULA_CERT_BIN)
        NEBULA_BIN.chmod(0o755)
        NEBULA_CERT_BIN.chmod(0o755)
        NEBULA_CONF_DIR.mkdir(parents=True, exist_ok=True)
        log(f"  ✓ nebula + nebula-cert binaries installed ({arch})")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        log(f"  WARNING: nebula download skipped — Nebula unavailable until installed ({e})")


def _reconcile_optin_services() -> None:
    """
    snmpd enables+starts itself on fresh apt install. Keep it opt-in: if SNMP
    isn't enabled in config, stop+disable it (apply() manages it once the user
    turns it on). Never touches it when SNMP is enabled — that's apply()'s job.
    """
    try:
        state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except (OSError, json.JSONDecodeError):
        state = {}
    if not state.get("snmp", {}).get("enabled"):
        subprocess.run(["systemctl", "disable", "--now", "snmpd"], check=False, capture_output=True, text=True)


def _provision_packages(extract_dir: Path) -> None:
    """
    Ensure the apt packages listed in deploy/packages are installed. Idempotent
    (`apt-get install` on a present package is a no-op) and best-effort: a
    failure — offline, or dpkg locked by unattended-upgrades — is logged, never
    fatal, since the code update already succeeded.
    """
    src = extract_dir / "deploy" / "packages"
    if not src.exists():
        return
    pkgs = [
        ln.strip() for ln in src.read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not pkgs:
        return
    try:
        proc = subprocess.run(
            ["apt-get", "install", "-y", "-q", *pkgs],
            capture_output=True, text=True, timeout=600,
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log(f"  WARNING: package install skipped — features needing new packages may be unavailable ({e})")
        return
    if proc.returncode == 0:
        log(f"  ✓ packages ensured ({len(pkgs)})")
        _reconcile_optin_services()
    else:
        log(f"  WARNING: apt-get install returned {proc.returncode} — features needing new packages may be unavailable: {proc.stderr.strip()[:200]}")


def _provision_frr() -> None:
    """
    Mirror install.sh's FRR setup (issue #143) for devices that gained BGP
    support via OTA rather than a fresh install: enable bgpd in
    /etc/frr/daemons (off by default in the frr apt package) and join the
    spud-router service user to the frrvty/frr groups so read-only vtysh
    status queries work without a sudo grant. Idempotent (sed on an
    already-"yes" line is a no-op; usermod -aG is safe to repeat) and
    best-effort — frr may not be installed yet on this call if the apt
    install itself failed, in which case this just logs and moves on.
    """
    daemons_file = Path("/etc/frr/daemons")
    if not daemons_file.exists():
        return
    try:
        content = daemons_file.read_text()
        if "bgpd=no" in content:
            daemons_file.write_text(content.replace("bgpd=no", "bgpd=yes", 1))
            log("  ✓ bgpd enabled in /etc/frr/daemons")
    except OSError as e:
        log(f"  WARNING: could not update /etc/frr/daemons ({e})")
        return

    groups = []
    for g in ("frrvty", "frr"):
        check = subprocess.run(["getent", "group", g], capture_output=True, text=True)
        if check.returncode == 0:
            groups.append(g)
    if groups:
        subprocess.run(["usermod", "-aG", ",".join(groups), "spud-router"], check=False, capture_output=True, text=True)
        log(f"  ✓ spud-router joined groups: {', '.join(groups)}")
    else:
        log("  WARNING: frrvty/frr groups not found — vtysh status queries may need sudo")


def _provision_system(extract_dir: Path) -> None:
    """
    Bring system-level dependencies up to what the installed version needs, so
    features added since this device's last install.sh run work over OTA:
    sudoers grants, systemd units, the dnsproxy/nebula binaries, apt
    packages, and FRR's bgpd/group setup.

    Every step is idempotent and best-effort — a failure is logged but never
    aborts the update (the app code is already in place). sudoers is the one
    exception in importance and is always validated with `visudo -c` before
    replacement, so a bad policy can never lock the service out of sudo.
    """
    log("Provisioning system dependencies…")
    _provision_sudoers(extract_dir)
    _provision_systemd_units(extract_dir)
    _provision_staging_env()
    _provision_dnsproxy_binary()
    _provision_nebula_binaries()
    _provision_packages(extract_dir)
    # After packages: frr itself must already be apt-installed (via
    # _provision_packages, deploy/packages) before /etc/frr/daemons exists.
    _provision_frr()


def _provision_only(extract_dir: Path) -> int:
    """
    Run provisioning (sudoers, systemd units, binaries, apt packages)
    against `extract_dir`, then return — no download/backup/restart/
    health-gate. Best-effort by construction: `_refresh_privileged_files()`
    and `_provision_system()` are themselves already non-fatal/logged, so
    there is nothing here to catch — a provisioning hiccup was never
    allowed to raise past those functions in the first place.

    This is the dispatch target for `update.py --provision-only <dir>`,
    which install_new() invokes against the *newly-installed* update.py
    right after the code copy (see #113): running provisioning from the
    old, currently-executing update.py would mean a release's own new
    provisioning steps (a new sudoers grant, a new systemd unit, a new
    binary download) don't take effect until the *next* update, since the
    old code doesn't know about them yet.
    """
    log(f"Provisioning from {extract_dir}…")
    _refresh_privileged_files(extract_dir)
    _provision_system(extract_dir)
    log("✓ Provisioning complete.")
    return 0


def install_new(extract_dir: Path) -> None:
    """
    Copy the release into place, then hand provisioning off to the
    newly-installed update.py (`--provision-only`) rather than running it
    from this — the currently-executing, about-to-be-replaced — copy. See
    _provision_only()'s docstring and issue #113 for why: provisioning
    from the old code would silently skip whatever new provisioning steps
    this release itself introduces until a subsequent update.

    Non-fatal + logged: a provisioning hiccup must never fail the update
    or trigger a rollback (existing best-effort policy for provisioning —
    see _provision_system()'s own docstring). Provisioning is idempotent,
    so re-running it (e.g. on a retried update) is always safe.

    Residual one-cycle lag: the updater instance performing an *install*
    must itself already contain this re-exec logic to take advantage of
    it. A device on a pre-#113 update.py updating to a post-#113 release
    still provisions with the old (pre-#113) code for that one hop — it
    starts re-exec'ing correctly from its *next* update onward, once that
    update.py is itself what's installed. There is no fix for that first
    hop short of manual intervention (or an install.sh re-run); it is a
    one-time, self-resolving transition cost, not an ongoing gap.
    """
    _copy_release_files(extract_dir)

    updater = INSTALL_DIR / "update.py"
    try:
        proc = subprocess.run(
            ["/usr/bin/python3", str(updater), "--provision-only", str(extract_dir)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            log(f"  WARNING: post-install provisioning returned {proc.returncode}: {proc.stderr.strip()[:200]}")
    except OSError as e:
        # Couldn't even launch the subprocess (missing interpreter/updater,
        # permissions, …) — still non-fatal: the code copy already
        # succeeded, and provisioning is best-effort by design (see this
        # function's docstring). The device just won't have picked up any
        # *new* provisioning steps this release introduced until the next
        # update — no worse than the pre-#113 behavior this fix improves on.
        log(f"  WARNING: could not launch post-install provisioning: {e}")


# ── Health gate ────────────────────────────────────────────────────────────────

def _service_active() -> bool:
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", "spud-router"],
        ).returncode == 0
    except Exception:
        return False


def _fetch_health() -> dict | None:
    """GET /api/health over the device's own self-signed HTTPS listener."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(
            HEALTH_URL,
            headers={"User-Agent": "spud-router-updater/1.0"},
        )
        with urllib.request.urlopen(req, timeout=3, context=ctx) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def health_gate(target_version: str, timeout: float = 60, poll_interval: float = 2) -> bool:
    """
    Poll until the service is active AND /api/health reports the target
    version, or timeout elapses. Returns False on timeout (never raises).
    """
    deadline = time.time() + timeout
    while True:
        if _service_active():
            data = _fetch_health()
            if data and data.get("version") == target_version:
                return True
        if time.time() >= deadline:
            return False
        time.sleep(poll_interval)


def write_tls_restart_status(**fields) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    TLS_RESTART_STATUS_FILE.write_text(json.dumps(fields))


def tls_restart() -> int:
    """
    Restart spud-router to pick up a newly uploaded/regenerated TLS
    certificate, rolling back to the previous pair automatically if the
    service doesn't come back up healthy. Runs detached (run-update.sh
    tls-restart → systemd-run → this), so it survives the
    `systemctl restart spud-router` it performs — same pattern as
    apply_update()'s own self-restart.
    """
    version = current_version()
    write_tls_restart_status(state="restarting")
    log("Restarting spud-router to activate the new TLS certificate…")
    subprocess.run(["systemctl", "restart", "spud-router"], capture_output=True, text=True)

    if health_gate(version, timeout=30):
        write_tls_restart_status(state="ok", message="New certificate is live.")
        log("✓ New certificate is live.")
        return 0

    log("New certificate did not come up healthy — restoring the previous pair…")
    restored = False
    if TLS_CERT_BAK.exists() and TLS_KEY_BAK.exists():
        shutil.copy2(TLS_CERT_BAK, TLS_CERT)
        shutil.copy2(TLS_KEY_BAK, TLS_KEY)
        TLS_KEY.chmod(0o600)
        TLS_CERT.chmod(0o644)
        subprocess.run(["systemctl", "restart", "spud-router"], capture_output=True, text=True)
        restored = health_gate(version, timeout=30)

    if restored:
        write_tls_restart_status(
            state="rolledback",
            message="New certificate failed to come up; restored the previous certificate.",
        )
        log("✓ Rolled back to the previous certificate.")
        return 1

    write_tls_restart_status(
        state="failed",
        message="Service did not come back up even after restoring the previous certificate — "
                 "check manually (systemctl status spud-router).",
    )
    log("ERROR: rollback restart did not pass the health check — manual attention needed")
    return 1
def write_commit_status(**fields) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    COMMIT_STATUS_FILE.write_text(json.dumps(fields))


def revert_config() -> int:
    """
    Restore the pre-apply state.json snapshot and re-activate it directly
    (this runs as root under systemd-run — see deploy/spud-commit.sh's
    "revert" subcommand, fired by the timer "arm" schedules — so no sudo is
    needed for any of the writes/restarts activate_all() performs).

    A missing snapshot means the apply was already confirmed (confirm()
    deletes it) or nothing was ever armed — not an error, just a no-op.
    """
    if not ROLLBACK_STATE_FILE.exists():
        log("No rollback snapshot found — nothing to revert (already confirmed?).")
        return 0

    try:
        state = json.loads(ROLLBACK_STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log(f"ERROR: could not read rollback snapshot: {e}")
        write_commit_status(state="revert_failed", message=f"Could not read rollback snapshot: {e}")
        return 1

    # apply_core.py is fastapi-free but lives under backend/ — add the
    # install root to sys.path so `backend.apply_core` resolves under the
    # system python3 this script runs under (mirrors install.sh's own
    # bootstrap snippet, which imports backend.generators the same way).
    sys.path.insert(0, str(INSTALL_DIR))
    from backend.apply_core import activate_all  # noqa: E402

    STATE_FILE.write_text(json.dumps(state, indent=2))
    log("Restored state.json from the pre-apply snapshot.")

    try:
        steps = activate_all(state, sudo=False)
        for s in steps:
            log(f"  {s}")
        log("✓ Reverted and re-activated the pre-apply configuration.")
        ROLLBACK_STATE_FILE.unlink(missing_ok=True)
        ARM_STATUS_FILE.unlink(missing_ok=True)
        write_commit_status(
            state="reverted",
            message="Auto-reverted to the previous configuration — the confirmation window expired.",
        )
        return 0
    except RuntimeError as e:
        log(f"ERROR: revert activation failed: {e}")
        write_commit_status(state="revert_failed", message=f"Revert activation failed: {e}")
        return 1


# ── Rollback ───────────────────────────────────────────────────────────────────

def rollback(manifest: list[dict], from_version: str) -> bool:
    """
    Restore the pre-update snapshot, restart the service on the old version,
    and confirm it comes back healthy. Sets status to 'rolledback' on
    success or 'failed' if even the restored version won't come up healthy
    (rare — means manual attention is needed).
    """
    write_status(phase="rollback", percent=90)
    log("  Restoring previous install…")
    restore_backup(manifest)
    VERSION_FILE.write_text(from_version)
    log(f"  ✓ Restored v{from_version}")

    log("  Restarting spud-router service…")
    subprocess.run(["systemctl", "restart", "spud-router"], capture_output=True, text=True)

    if health_gate(from_version, timeout=30):
        write_status(
            state="rolledback", phase="done", percent=100,
            message=f"Update failed and was rolled back to v{from_version} (previous version, confirmed running).",
        )
        log(f"✓ Rolled back to {from_version}")
        return True

    write_status(
        state="failed", phase="done", percent=100,
        message=(
            f"Update failed; rollback restored v{from_version} files but the service did not "
            "come back healthy — check manually (systemctl status spud-router)."
        ),
    )
    log("ERROR: rollback restart did not pass the health check — manual attention needed")
    return False


# ── Apply ──────────────────────────────────────────────────────────────────────

def apply_update(release: dict) -> int:
    """
    Download, verify, extract, install, restart, and health-gate a release.
    Any failure (an exception, or a failed health-gate) triggers an
    automatic rollback to the version that was running before this call, as
    long as backup_current() completed. Never returns with the status file
    left in state="running".
    """
    from_version = current_version()
    to_version   = release["version"]
    write_status(to_version=to_version, phase="backup", percent=5)

    manifest: list[dict] | None = None
    try:
        log(f"[1/6] Backing up current install (v{from_version})…")
        manifest = backup_current()
        log(f"  ✓ Backup saved to {BACKUP_DIR}")

        with tempfile.TemporaryDirectory(prefix="spud-update-") as tmpdir:
            tmp   = Path(tmpdir)
            tball = tmp / "release.tar.gz"

            write_status(phase="download", percent=15)
            log(f"[2/6] Downloading {release['tag']}…")
            download_file(release["tarball_url"], tball)

            write_status(phase="verify", percent=30)
            log("[3/6] Verifying checksum…")
            if release.get("sha256"):
                if not verify_checksum(tball, release["sha256"]):
                    raise RuntimeError("Checksum mismatch — aborting")
                log("  ✓ Checksum OK")
            else:
                log("  ⚠ No checksum file in release — skipping verification")

            write_status(phase="extract", percent=40)
            log("[4/6] Extracting…")
            extract_dir = tmp / "extracted"
            extract_dir.mkdir()
            _extract_tarball(tball, extract_dir)
            log("  ✓ Extracted")

            write_status(phase="install", percent=55)
            log("[5/6] Installing files…")
            install_new(extract_dir)

            VERSION_FILE.write_text(to_version)
            log(f"  ✓ Version updated to {to_version}")

        write_status(phase="restart", percent=75)
        log("[6/6] Restarting spud-router service…")
        result = subprocess.run(["systemctl", "restart", "spud-router"], capture_output=True, text=True)
        if result.returncode != 0:
            log(f"  WARNING: service restart command failed: {result.stderr.strip()}")

        write_status(phase="health", percent=85)
        log("  Waiting for health check…")
        if health_gate(to_version):
            prune_backup()
            write_status(
                state="success", phase="done", percent=100,
                message=f"Update to {to_version} complete (confirmed running).",
            )
            log(f"\n✓ spud-router updated to {to_version}")
            return 0

        log("  Health check failed — rolling back…")
        rollback(manifest, from_version)
        return 1

    except Exception as e:
        log(f"  ERROR: {e}")
        if manifest is not None:
            log("  Rolling back due to error…")
            try:
                rollback(manifest, from_version)
            except Exception as rb_exc:
                log(f"  ERROR: rollback also failed: {rb_exc}")
                write_status(
                    state="failed", phase="done", percent=100,
                    message=f"Update failed and rollback also failed: {rb_exc}. Manual attention needed.",
                )
        else:
            write_status(
                state="failed", phase="done", percent=100,
                message=f"Update failed before any changes were made: {e}",
            )
        return 1


# ── CLI entry point ───────────────────────────────────────────────────────────
def main() -> int:
    cfg     = load_update_config()
    owner   = cfg.get("github_owner", DEFAULT_CONFIG["github_owner"])
    repo    = cfg.get("github_repo",  DEFAULT_CONFIG["github_repo"])
    current = current_version()

    if _update_already_running():
        # Don't touch the status file — it belongs to the in-flight run.
        print(f"ERROR: an update is already running ({UPDATE_UNIT} is active)", file=sys.stderr)
        return 1

    _start_status(current)
    log("spud-router updater")
    log(f"  Current version : {current}")
    log(f"  Repository      : {owner}/{repo}")
    log("")

    log("Checking for updates…")
    try:
        release = get_latest_release(owner, repo)
    except urllib.error.URLError as e:
        log(f"ERROR: Cannot reach GitHub: {e}")
        write_status(state="failed", phase="done", message=f"Cannot reach GitHub: {e}")
        return 1
    except Exception as e:
        log(f"ERROR: {e}")
        write_status(state="failed", phase="done", message=str(e))
        return 1

    log(f"  Latest version  : {release['version']}")

    if release["version"] == current:
        log("\n✓ Already up to date.")
        write_status(
            state="success", phase="done", percent=100,
            to_version=current, message="Already up to date.",
        )
        return 2

    log(f"\nRelease notes for {release['tag']}:")
    for line in release["changelog"].splitlines()[:20]:
        log(f"  {line}")
    if len(release["changelog"].splitlines()) > 20:
        log("  …(truncated)")
    log("")

    return apply_update(release)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="spud-router updater")
    parser.add_argument(
        "--apply", action="store_true",
        help="Explicit non-interactive apply (used by run-update.sh). "
             "Behavior is identical to running with no arguments.",
    )
    parser.add_argument(
        "--tls-restart", action="store_true",
        help="Restart spud-router to activate a newly uploaded/regenerated "
             "TLS certificate, rolling back to the previous pair if the "
             "service doesn't come back up healthy (used by "
             "run-update.sh tls-restart).",
    )
    parser.add_argument(
        "--revert", action="store_true",
        help="Restore the pre-apply state.json snapshot and re-activate it "
             "(used by deploy/spud-commit.sh's detached auto-revert timer "
             "when an armed apply goes unconfirmed).",
    )
    parser.add_argument(
        "--provision-only", metavar="EXTRACT_DIR", default=None,
        help="Run provisioning (sudoers, systemd units, binaries, apt "
             "packages) against the given extracted-release directory, "
             "then exit — no download/backup/restart/health-gate. "
             "apply_update() invokes this against the newly-installed "
             "update.py right after copying release files, so a release's "
             "own provisioning steps run in the same update cycle instead "
             "of waiting for the next one (see issue #113).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("ERROR: Must run as root (sudo python3 update.py)", file=sys.stderr)
        sys.exit(1)
    args = _parse_args(sys.argv[1:])
    if args.tls_restart:
        sys.exit(tls_restart())
    if args.revert:
        sys.exit(revert_config())
    if args.provision_only:
        sys.exit(_provision_only(Path(args.provision_only)))
    sys.exit(main())
