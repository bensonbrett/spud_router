# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Nebula configuration and credential-import routes.

Scope is join-only (#91): this device never generates or signs Nebula
certs — it only imports a host cert/key + CA cert produced off-device by
`nebula-cert`, so there is no "create a new mesh" or "sign a peer" flow
here, unlike WireGuard's server-side peer-keypair generation.

Key material is write-only from the API's perspective, same pattern as
WireGuard/TLS/SNMP: GET replaces the stored host private key with
NEBULA_MASKED_SENTINEL (plus a `has_key` boolean); the host cert and CA
cert are NOT secret (they're public certificates) and are returned as-is,
alongside a parsed `cert_info`/`ca_info` (name/IPs/groups/validity) for
display, via `nebula-cert print -json`.

Credentials are imported and validated as a set through their own
POST/DELETE /credentials endpoints — never through PUT /api/nebula, which
always preserves whatever cert/key/CA is currently stored no matter what
the client echoes back in those fields. This mirrors PR1's TLS upload
flow: validate before ever touching the stored pair, and never let an
unrelated settings save silently swap out live credentials.
"""
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..models import NEBULA_MASKED_SENTINEL, NebulaConfig, NebulaCredentialsRequest
from ..state import load_state, save_state

router = APIRouter(prefix="/api/nebula", tags=["nebula"], dependencies=[Depends(require_auth)])

_SUBPROCESS_TIMEOUT = 15


def _cert_info_from_path(path: Path) -> dict | None:
    """Best-effort parse via `nebula-cert print -json` — returns None if
    the binary is missing or the file doesn't parse, never raises. Used
    for display only; real acceptance checks happen in _validate_credentials."""
    try:
        proc = subprocess.run(
            ["nebula-cert", "print", "-json", "-path", str(path)],
            capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None

    details   = raw.get("details", {})
    not_after = details.get("notAfter")
    expired   = False
    if not_after:
        try:
            expired = datetime.fromisoformat(not_after.replace("Z", "+00:00")) < datetime.now(timezone.utc)
        except ValueError:
            pass

    return {
        "name":       details.get("name"),
        "ips":        details.get("ips", []),
        "groups":     details.get("groups", []),
        "issuer":     details.get("issuer"),
        "not_before": details.get("notBefore"),
        "not_after":  not_after,
        "is_ca":      details.get("isCa", False),
        "expired":    expired,
    }


def _cert_info_from_pem(pem: str) -> dict | None:
    if not pem:
        return None
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "cert.crt"
        path.write_text(pem)
        return _cert_info_from_path(path)


def _smoke_test_yaml(ca_path: Path, cert_path: Path, key_path: Path, listen_port: int) -> str:
    """A minimal config.yaml, just enough for `nebula -test` to load and
    cross-check the pki section — not the real generators/nebula.py output
    (that one hardcodes the real /etc/nebula/* install paths)."""
    return "\n".join([
        "pki:",
        f'  ca: "{ca_path}"',
        f'  cert: "{cert_path}"',
        f'  key: "{key_path}"',
        "static_host_map: {}",
        "lighthouse:",
        "  am_lighthouse: false",
        "listen:",
        f"  port: {listen_port}",
        "tun:",
        "  disabled: false",
        "  dev: nebula1",
        "firewall:",
        "  outbound:",
        "    - port: any",
        "      proto: any",
        "      host: any",
        "  inbound: []",
        "",
    ])


def _validate_credentials(cert_pem: str, key_pem: str, ca_pem: str, listen_port: int) -> dict:
    """
    Reject before ever touching stored state: the host cert must verify
    against the given CA (`nebula-cert verify`), must not be expired, and
    the full cert/key/CA/config combination must be accepted by `nebula
    -test` (which also catches a key that doesn't match the cert — nebula
    refuses to start on a mismatched pair). Returns the parsed cert_info
    on success; raises HTTPException(400) with the underlying tool's
    stderr on any failure.
    """
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        ca_path, cert_path, key_path = d / "ca.crt", d / "host.crt", d / "host.key"
        ca_path.write_text(ca_pem)
        cert_path.write_text(cert_pem)
        key_path.write_text(key_pem)

        try:
            verify = subprocess.run(
                ["nebula-cert", "verify", "-ca", str(ca_path), "-crt", str(cert_path)],
                capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="nebula-cert is not installed on this device")
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=500, detail="nebula-cert verify timed out")
        if verify.returncode != 0:
            detail = (verify.stderr or verify.stdout).strip()
            raise HTTPException(status_code=400, detail=f"Host certificate failed CA verification: {detail}")

        cert_info = _cert_info_from_path(cert_path)
        if cert_info and cert_info.get("expired"):
            raise HTTPException(status_code=400, detail="Host certificate has expired")

        config_path = d / "config.yaml"
        config_path.write_text(_smoke_test_yaml(ca_path, cert_path, key_path, listen_port))
        try:
            test = subprocess.run(
                ["nebula", "-test", "-config", str(config_path)],
                capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="nebula is not installed on this device")
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=500, detail="nebula -test timed out")
        if test.returncode != 0:
            detail = (test.stderr or test.stdout).strip()
            raise HTTPException(status_code=400, detail=f"Nebula rejected this cert/key/CA combination: {detail}")

        return cert_info


def _mask(nb: dict) -> dict:
    masked = dict(nb)
    masked["has_key"]  = bool(nb.get("key_pem"))
    masked["key_pem"]  = NEBULA_MASKED_SENTINEL if nb.get("key_pem") else ""
    masked["cert_info"] = _cert_info_from_pem(nb.get("cert_pem", ""))
    masked["ca_info"]   = _cert_info_from_pem(nb.get("ca_pem", ""))
    return masked


@router.get("")
def get_config():
    return _mask(load_state().get("nebula", {}))


@router.put("")
def set_config(config: NebulaConfig):
    """
    Updates everything except credentials — cert_pem/key_pem/ca_pem are
    always preserved from the currently-stored config regardless of what
    the client sends here, no matter the value. Import/replace/clear
    credentials only via POST/DELETE /api/nebula/credentials.
    """
    state = load_state()
    current = state.get("nebula", {})
    data = config.model_dump()
    data["cert_pem"] = current.get("cert_pem", "")
    data["key_pem"]  = current.get("key_pem", "")
    data["ca_pem"]   = current.get("ca_pem", "")
    state["nebula"] = data
    save_state(state)
    return {"ok": True}


@router.post("/credentials")
def set_credentials(req: NebulaCredentialsRequest):
    state = load_state()
    nb = dict(state.get("nebula", {}))
    cert_info = _validate_credentials(req.cert_pem, req.key_pem, req.ca_pem, nb.get("listen_port", 4242))
    nb["cert_pem"] = req.cert_pem
    nb["key_pem"]  = req.key_pem
    nb["ca_pem"]   = req.ca_pem
    state["nebula"] = nb
    save_state(state)
    return {"ok": True, "cert_info": cert_info}


@router.delete("/credentials")
def clear_credentials():
    state = load_state()
    nb = dict(state.get("nebula", {}))
    nb["cert_pem"] = ""
    nb["key_pem"]  = ""
    nb["ca_pem"]   = ""
    state["nebula"] = nb
    save_state(state)
    return {"ok": True}
