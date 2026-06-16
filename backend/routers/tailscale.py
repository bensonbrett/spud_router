"""Tailscale configuration and status routes."""
import json
import subprocess

from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..models import TailscaleConfig
from ..state import load_state, save_state

router = APIRouter(
    prefix="/api/tailscale",
    tags=["tailscale"],
    dependencies=[Depends(require_auth)],
)


@router.get("")
def get_config():
    return load_state().get("tailscale", {})


@router.post("")
def set_config(config: TailscaleConfig):
    state = load_state()
    state["tailscale"] = config.model_dump()
    save_state(state)
    return {"ok": True}


@router.get("/status")
def get_status():
    """Return live status from the tailscale binary."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        return {"error": result.stderr.strip()}
    except FileNotFoundError:
        return {"error": "tailscale not installed"}
    except Exception as e:
        return {"error": str(e)}


def apply(state: dict) -> list[str]:
    """
    Apply tailscale configuration by calling the tailscale CLI.
    Returns a list of result strings for the apply log.
    """
    ts = state.get("tailscale", {})

    if not ts.get("enabled"):
        subprocess.run(["tailscale", "down"], check=False)
        return ["Tailscale: down"]

    cmd = ["tailscale", "up"]
    if ts.get("accept_routes"):
        cmd.append("--accept-routes")
    if ts.get("advertise_routes"):
        cmd.append("--advertise-routes=" + ",".join(ts["advertise_routes"]))
    if ts.get("exit_node"):
        cmd.append("--advertise-exit-node")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return ["Tailscale: up"]
    return [f"Tailscale warning: {result.stderr.strip()}"]
