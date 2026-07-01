"""
Update management routes.

GET  /api/update/check  — check GitHub for a newer release
POST /api/update/apply  — stream update progress as Server-Sent Events
"""
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..auth import require_auth
from ..update import DEFAULT_CONFIG

router = APIRouter(
    prefix="/api/update",
    tags=["update"],
    dependencies=[Depends(require_auth)],
)

INSTALL_DIR        = Path("/opt/spud-router")
VERSION_FILE       = INSTALL_DIR / "VERSION"
UPDATE_CONFIG_FILE = Path("/etc/spud-router/update.json")
UPDATE_SCRIPT      = INSTALL_DIR / "update.py"


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


@router.post("/apply")
def apply_update():
    """
    Run the update script and stream its output as Server-Sent Events.

    The client receives a stream of:
        data: {"line": "...", "done": false}\n\n
        data: {"line": "...", "done": true, "exit_code": 0}\n\n

    Exit codes from update.py:
        0 — success
        1 — failed
        2 — already up to date
    """
    if not UPDATE_SCRIPT.exists():
        def error_stream():
            yield f'data: {json.dumps({"line": "ERROR: update.py not found at " + str(UPDATE_SCRIPT), "done": True, "exit_code": 1})}\n\n'
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    def stream_update():
        proc = subprocess.Popen(
            ["python3", str(UPDATE_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip("\n")
            yield f"data: {json.dumps({'line': line, 'done': False})}\n\n"

        proc.wait()
        yield f"data: {json.dumps({'line': '', 'done': True, 'exit_code': proc.returncode})}\n\n"

    return StreamingResponse(stream_update(), media_type="text/event-stream")
