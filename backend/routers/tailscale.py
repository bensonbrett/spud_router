"""Tailscale configuration and status routes."""
import ipaddress
import json
import subprocess

from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..models import AuthKeyRequest, TailscaleConfig
from ..state import TAILSCALE_AUTHKEY_FILE, load_state, save_state
from ..tailscale_apply import apply, has_authkey as _has_authkey

router = APIRouter(
    prefix="/api/tailscale",
    tags=["tailscale"],
    dependencies=[Depends(require_auth)],
)


def _save_authkey(key: str) -> None:
    TAILSCALE_AUTHKEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    TAILSCALE_AUTHKEY_FILE.write_text(key.strip() + "\n")
    TAILSCALE_AUTHKEY_FILE.chmod(0o600)


def _clear_authkey() -> None:
    TAILSCALE_AUTHKEY_FILE.unlink(missing_ok=True)


@router.get("")
def get_config():
    config = dict(load_state().get("tailscale", {}))
    config["has_auth_key"] = _has_authkey()
    return config


@router.post("")
def set_config(config: TailscaleConfig):
    state = load_state()
    state["tailscale"] = config.model_dump()
    save_state(state)
    return {"ok": True}


@router.post("/authkey")
def set_authkey(req: AuthKeyRequest):
    _save_authkey(req.auth_key)
    state = load_state()
    if state.get("tailscale", {}).get("enabled"):
        results = apply(state)
        return {"ok": True, "steps": results}
    return {"ok": True}


@router.delete("/authkey")
def delete_authkey():
    _clear_authkey()
    return {"ok": True, "message": "Auth key cleared. This does not log the node out of the tailnet."}


@router.get("/candidate-routes")
def candidate_routes():
    """Return advertisable subnet CIDRs derived from configured LAN VLANs and mgmt subnet."""
    state = load_state()
    candidates: list[dict] = []
    seen: set[str] = set()

    def _add(ip: str, prefix, label: str, source: str) -> None:
        if not ip or not prefix:
            return
        try:
            network = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
        except ValueError:
            return
        cidr = str(network)
        if cidr in seen:
            return
        seen.add(cidr)
        candidates.append({"cidr": cidr, "label": label, "source": source})

    for vlan in state.get("vlans", []):
        _add(
            vlan.get("ip_address"),
            vlan.get("prefix_len"),
            f"VLAN {vlan.get('vlan_id')} · {vlan.get('name')}",
            "vlan",
        )

    router_cfg = state.get("router", {})
    if router_cfg.get("mgmt_enabled"):
        _add(router_cfg.get("mgmt_ip"), router_cfg.get("mgmt_prefix"), "Management", "mgmt")

    return candidates


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

