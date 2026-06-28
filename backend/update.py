#!/usr/bin/env python3
"""
spud-router updater — standalone update script.

Can be run directly from SSH:
    sudo python3 /opt/spud-router/update.py

Or called by the backend via subprocess for web UI / CLI updates.
Streams progress to stdout line-by-line so callers can show live output.

Exit codes:
    0 — success
    1 — update failed
    2 — already up to date
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import typing
import urllib.error
import urllib.request
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
UPDATE_CONFIG_FILE = Path("/etc/spud-router/update.json")
INSTALL_DIR        = Path("/opt/spud-router")
VERSION_FILE       = INSTALL_DIR / "VERSION"

DEFAULT_CONFIG = {
    "github_owner": "bensonbrett",
    "github_repo":  "spud-router",
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


def log(msg: str) -> None:
    """Print a progress line. Flushed immediately for streaming consumers."""
    print(msg, flush=True)


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


def apply_update(release: dict) -> int:
    """
    Download, verify, extract, and install the release tarball.
    Returns 0 on success, 1 on failure.
    """
    with tempfile.TemporaryDirectory(prefix="spud-update-") as tmpdir:
        tmp   = Path(tmpdir)
        tball = tmp / "release.tar.gz"

        # Download
        log(f"[1/5] Downloading {release['tag']}…")
        try:
            download_file(release["tarball_url"], tball)
        except Exception as e:
            log(f"  ERROR: Download failed: {e}")
            return 1

        # Verify checksum if available
        log("[2/5] Verifying checksum…")
        if release.get("sha256"):
            if not verify_checksum(tball, release["sha256"]):
                log("  ERROR: Checksum mismatch — aborting")
                return 1
            log("  ✓ Checksum OK")
        else:
            log("  ⚠ No checksum file in release — skipping verification")

        # Extract
        log("[3/5] Extracting…")
        extract_dir = tmp / "extracted"
        extract_dir.mkdir()
        try:
            with tarfile.open(tball) as tf:
                # Use filter='data' on Python 3.12+ to block symlink/device
                # attacks; fall back to a manual check on older versions.
                if sys.version_info >= (3, 12):
                    tf.extractall(extract_dir, filter="data")
                else:
                    for member in tf.getmembers():
                        mp = Path(member.name)
                        if mp.is_absolute() or ".." in mp.parts:
                            log(f"  ERROR: Unsafe path in tarball: {member.name}")
                            return 1
                    tf.extractall(extract_dir)
        except Exception as e:
            log(f"  ERROR: Extraction failed: {e}")
            return 1
        log("  ✓ Extracted")

        # Install files
        log("[4/5] Installing files…")
        try:
            _install_files(extract_dir)
        except Exception as e:
            log(f"  ERROR: Install failed: {e}")
            return 1

        # Write new version
        VERSION_FILE.write_text(release["version"])
        log(f"  ✓ Version updated to {release['version']}")

    # Restart service
    log("[5/5] Restarting spud-router service…")
    result = subprocess.run(
        ["systemctl", "restart", "spud-router"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"  WARNING: Service restart failed: {result.stderr.strip()}")
        log("  Try manually: systemctl restart spud-router")
    else:
        log("  ✓ Service restarted")

    log(f"\n✓ spud-router updated to {release['version']}")
    return 0


def _install_files(extract_dir: Path) -> None:
    """
    Copy files from the extracted tarball into the install directory.

    Tarball layout:
        install.sh
        main.py
        spud-cli
        ssh-banner
        motd
        index.html
        assets/          (optional Vite chunks)

    We skip install.sh — it's for fresh installs only.
    """
    file_map = {
        "main.py":    INSTALL_DIR / "main.py",
        "spud-cli":   Path("/usr/local/bin/spud-cli"),
        "ssh-banner": Path("/etc/ssh/spud-router-banner"),
        "motd":       Path("/etc/update-motd.d/99-spud-router"),
        "index.html": INSTALL_DIR / "static" / "index.html",
    }

    for src_name, dest in file_map.items():
        src = extract_dir / src_name
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            log(f"  ✓ {src_name} → {dest}")
        else:
            log(f"  - {src_name} not in release (skipped)")

    # Assets directory (Vite JS/CSS chunks)
    src_assets  = extract_dir / "assets"
    dest_assets = INSTALL_DIR / "static" / "assets"
    if src_assets.exists():
        if dest_assets.exists():
            shutil.rmtree(dest_assets)
        shutil.copytree(src_assets, dest_assets)
        log(f"  ✓ assets/ → {dest_assets}")

    # Ensure spud-cli is executable
    spud_cli = Path("/usr/local/bin/spud-cli")
    if spud_cli.exists():
        spud_cli.chmod(0o755)

    # Ensure motd is executable
    motd = Path("/etc/update-motd.d/99-spud-router")
    if motd.exists():
        motd.chmod(0o755)


# ── CLI entry point ───────────────────────────────────────────────────────────
def main() -> int:
    cfg     = load_update_config()
    owner   = cfg.get("github_owner", DEFAULT_CONFIG["github_owner"])
    repo    = cfg.get("github_repo",  DEFAULT_CONFIG["github_repo"])
    current = current_version()

    log(f"spud-router updater")
    log(f"  Current version : {current}")
    log(f"  Repository      : {owner}/{repo}")
    log("")

    log("Checking for updates…")
    try:
        release = get_latest_release(owner, repo)
    except urllib.error.URLError as e:
        log(f"ERROR: Cannot reach GitHub: {e}")
        return 1
    except Exception as e:
        log(f"ERROR: {e}")
        return 1

    log(f"  Latest version  : {release['version']}")

    if release["version"] == current:
        log("\n✓ Already up to date.")
        return 2

    log(f"\nRelease notes for {release['tag']}:")
    for line in release["changelog"].splitlines()[:20]:
        log(f"  {line}")
    if len(release["changelog"].splitlines()) > 20:
        log("  …(truncated)")
    log("")

    return apply_update(release)


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("ERROR: Must run as root (sudo python3 update.py)", file=sys.stderr)
        sys.exit(1)
    sys.exit(main())
