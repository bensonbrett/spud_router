# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
WireGuard configuration and peer management routes.

Key material is write-only from the API's perspective: GET replaces a
stored private key with WG_MASKED_SENTINEL (plus a `has_key` boolean and
the derived public key), and PUT treats that same sentinel as "leave the
stored key unchanged" — same pattern as SNMP's community masking and the
Tailscale authkey. A newly *generated* peer keypair is the one exception:
its private key is returned exactly once in the POST /api/wireguard/peers
response (for the admin to hand to that client) and is never persisted —
the router only ever stores peers' public keys.
"""
import base64
import secrets
import subprocess

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..models import (
    WG_MASKED_SENTINEL, WireguardConfig, WireguardPeer, WireguardPeerCreateRequest,
)
from ..state import load_state, save_state
from ..vpn_coexistence import validate_single_route_all

router = APIRouter(prefix="/api/wireguard", tags=["wireguard"], dependencies=[Depends(require_auth)])


def _run_wg(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["wg", *args], input=input_text, capture_output=True, text=True)


def _derive_pubkey(private_key: str) -> str:
    proc = _run_wg("pubkey", input_text=private_key)
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Could not derive public key: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _generate_keypair() -> tuple[str, str]:
    proc = _run_wg("genkey")
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Could not generate a key: {proc.stderr.strip()}")
    private_key = proc.stdout.strip()
    return private_key, _derive_pubkey(private_key)


def _mask(wg: dict) -> dict:
    masked = dict(wg)
    masked["has_key"] = bool(wg.get("private_key"))
    masked["private_key"] = WG_MASKED_SENTINEL if wg.get("private_key") else ""
    return masked


def _qr_png_data_uri(text: str) -> str | None:
    """
    Best-effort PNG QR code of a client .conf, as a data URI the web UI can
    drop straight into an <img src>. Shells out to `qrencode` (an apt
    package, see deploy/packages) rather than pulling in a frontend QR
    library — this only ever runs at peer-creation time, alongside the
    one-time private key reveal, so a missing/failed qrencode just means
    no QR image (the .conf text and copy button still work).
    """
    try:
        proc = subprocess.run(
            ["qrencode", "-t", "PNG", "-o", "-"],
            input=text.encode(), capture_output=True,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    return "data:image/png;base64," + base64.b64encode(proc.stdout).decode()


def _client_config_text(state: dict, peer_private_key: str, peer_address: str) -> str:
    """Build the .conf a peer would use to connect TO this router — the
    router's own public key + WAN endpoint as that peer's [Peer] section."""
    wg = state.get("wireguard", {})
    router_cfg = state.get("router", {})
    host = router_cfg.get("wan_ip") or router_cfg.get("hostname", "")
    lines = [
        "[Interface]",
        f"PrivateKey = {peer_private_key}",
        f"Address = {peer_address}",
        "",
        "[Peer]",
        f"PublicKey = {wg.get('public_key', '')}",
        f"Endpoint = {host}:{wg.get('listen_port', 51820)}",
        "AllowedIPs = 0.0.0.0/0",
        "PersistentKeepalive = 25",
    ]
    return "\n".join(lines) + "\n"


@router.get("")
def get_config():
    return _mask(load_state().get("wireguard", {}))


@router.put("")
def set_config(config: WireguardConfig):
    state = load_state()
    current = state.get("wireguard", {})
    data = config.model_dump()

    if data["private_key"] == WG_MASKED_SENTINEL:
        data["private_key"] = current.get("private_key", "")
        data["public_key"] = current.get("public_key", "")
    elif data["private_key"]:
        data["public_key"] = _derive_pubkey(data["private_key"])
    elif data["enabled"]:
        # Enabling with no key pasted/kept — generate one so the interface
        # actually has an identity, mirroring TLS's own first-run generate.
        private_key, public_key = _generate_keypair()
        data["private_key"] = private_key
        data["public_key"] = public_key
    else:
        data["public_key"] = ""

    prospective_state = dict(state)
    prospective_state["wireguard"] = data
    try:
        validate_single_route_all(prospective_state)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    state["wireguard"] = data
    save_state(state)
    return {"ok": True}


@router.post("/regenerate-key")
def regenerate_key():
    """Issue a fresh keypair for this device's own WireGuard interface,
    replacing whatever was there. Existing peers keep working once they're
    told the new public key (out of band) — this endpoint doesn't touch
    peers."""
    state = load_state()
    private_key, public_key = _generate_keypair()
    wg = dict(state.get("wireguard", {}))
    wg["private_key"] = private_key
    wg["public_key"] = public_key
    state["wireguard"] = wg
    save_state(state)
    return {"ok": True, "public_key": public_key}


# ── Peers ─────────────────────────────────────────────────────────────────────

@router.get("/peers")
def list_peers():
    return load_state().get("wireguard", {}).get("peers", [])


@router.post("/peers")
def add_peer(req: WireguardPeerCreateRequest):
    state = load_state()
    wg = dict(state.get("wireguard", {}))
    peers = list(wg.get("peers", []))

    generated_private_key = None
    public_key = req.public_key
    if public_key is None:
        generated_private_key, public_key = _generate_keypair()

    peer = WireguardPeer(
        id=secrets.token_hex(4),
        name=req.name,
        public_key=public_key,
        allowed_ips=req.allowed_ips,
        endpoint=req.endpoint,
        persistent_keepalive=req.persistent_keepalive,
    )
    peers.append(peer.model_dump())
    wg["peers"] = peers
    state["wireguard"] = wg
    save_state(state)

    result = {"ok": True, "peer": peer.model_dump()}
    if generated_private_key:
        client_config = _client_config_text(state, generated_private_key, req.client_address)
        result["private_key"] = generated_private_key
        result["client_config"] = client_config
        result["qr_png_base64"] = _qr_png_data_uri(client_config)
    return result


@router.delete("/peers/{peer_id}")
def delete_peer(peer_id: str):
    state = load_state()
    wg = dict(state.get("wireguard", {}))
    before = len(wg.get("peers", []))
    wg["peers"] = [p for p in wg.get("peers", []) if p.get("id") != peer_id]
    state["wireguard"] = wg
    save_state(state)
    return {"removed": before - len(wg["peers"])}
