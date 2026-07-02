"""
System-level routes: unauthenticated health probe and remote reboot.

GET  /api/health          — unauthenticated; used by the updater's
                             health-gate and the post-update UI confirmation.
POST /api/system/reboot   — authed; reboots the device via the same scoped
                             root wrapper the updater uses.
"""
import subprocess

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..update import RUN_UPDATE_SCRIPT, VERSION_FILE

router = APIRouter(tags=["system"])


def _current_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return "unknown"


@router.get("/api/health")
def health():
    """
    Deliberately minimal and side-effect free — the only unauthenticated
    endpoint in the app. Returns nothing beyond status + version.
    """
    return {"status": "ok", "version": _current_version()}


@router.post("/api/system/reboot", dependencies=[Depends(require_auth)])
def reboot():
    """
    Reboot the device via the scoped root-owned wrapper. The wrapper delays
    ~2s before rebooting so this HTTP response reaches the client first.
    """
    result = subprocess.run(
        ["sudo", str(RUN_UPDATE_SCRIPT), "reboot"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to schedule reboot: {result.stderr.strip()}",
        )
    return {"rebooting": True}
