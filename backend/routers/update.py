"""
Update management routes.

GET  /api/update/check   — check GitHub for a newer release
POST /api/update/apply   — kick off a detached, self-healing update (backup →
                            install → restart → health-gate → auto-rollback
                            on failure). Returns immediately.
GET  /api/update/status  — poll progress; the status file written by
                            update.py is the single source of truth.
"""
import json
import subprocess
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..update import (
    DEFAULT_CONFIG,
    RUN_UPDATE_SCRIPT,
    STATUS_FILE,
    UPDATE_CONFIG_FILE,
    UPDATE_UNIT,
    VERSION_FILE,
)

router = APIRouter(
    prefix="/api/update",
    tags=["update"],
    dependencies=[Depends(require_auth)],
)


def _load_config() -> dict:
    if UPDATE_CONFIG_FILE.exists():
        try:
            return json.loads(UPDATE_CONFIG_FILE.read_text())
        except Exception:
            pass
    return DEFAULT_CONFIG


def _current_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return "unknown"


def _fetch_latest(owner: str, repo: str) -> dict:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "spud-router/1.0"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


@router.get("/check")
def check_for_update():
    """
    Check GitHub for the latest release and compare with the installed version.

    Returns:
        {
            current:    "1.0.0",
            latest:     "1.1.0",
            up_to_date: false,
            tag:        "v1.1.0",
            changelog:  "...",
            tarball_url:"https://...",
        }
    """
    cfg     = _load_config()
    owner   = cfg.get("github_owner", DEFAULT_CONFIG["github_owner"])
    repo    = cfg.get("github_repo",  DEFAULT_CONFIG["github_repo"])
    current = _current_version()

    try:
        release  = _fetch_latest(owner, repo)
        tag      = release["tag_name"]
        latest   = tag.lstrip("v")
        changelog = release.get("body", "No release notes.")

        # Find tarball asset URL
        tarball_url = None
        for asset in release.get("assets", []):
            if asset["name"].endswith(".tar.gz") and "spud-router" in asset["name"]:
                tarball_url = asset["browser_download_url"]
                break

        return {
            "current":     current,
            "latest":      latest,
            "up_to_date":  latest == current,
            "tag":         tag,
            "changelog":   changelog,
            "tarball_url": tarball_url,
        }

    except urllib.error.URLError as e:
        return {
            "current":    current,
            "latest":     None,
            "up_to_date": None,
            "error":      f"Cannot reach GitHub: {e.reason}",
        }
    except Exception as e:
        return {
            "current":    current,
            "latest":     None,
            "up_to_date": None,
            "error":      str(e),
        }


def _read_status() -> dict:
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"state": "idle"}


def _service_active(unit: str) -> bool:
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", unit],
        ).returncode == 0
    except Exception:
        return False


def _update_running() -> bool:
    """True if the detached updater unit is active or the status file says so."""
    if _service_active(UPDATE_UNIT):
        return True
    return _read_status().get("state") == "running"


@router.post("/apply")
def apply_update():
    """
    Kick off the update via the scoped root wrapper, which detaches the
    actual work into its own systemd unit (survives the service restart
    the update performs). Returns immediately — poll GET /api/update/status
    for progress.
    """
    if _update_running():
        raise HTTPException(status_code=409, detail="An update is already running")

    result = subprocess.run(
        ["sudo", str(RUN_UPDATE_SCRIPT), "apply"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start update: {result.stderr.strip()}",
        )
    return {"started": True}


@router.get("/status")
def update_status():
    """Polling target for the frontend/TUI — status file + live version/service state."""
    status = _read_status()
    return {
        **status,
        "installed_version": _current_version(),
        "service_active":    _service_active("spud-router"),
    }
