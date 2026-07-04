# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
System-level routes: unauthenticated health probe, remote reboot, and TLS
certificate management.

GET  /api/health          — unauthenticated; used by the updater's
                             health-gate and the post-update UI confirmation.
POST /api/system/reboot   — authed; reboots the device via the same scoped
                             root wrapper the updater uses.
GET  /api/system/tls                — current cert subject/SAN/issuer/expiry/
                                       fingerprint. Never the private key.
POST /api/system/tls                — upload a cert+key pair; validated
                                       (parses, not expired, key matches
                                       cert) before anything is written;
                                       restarts the service via a detached
                                       wrapper that rolls back to the
                                       previous pair if the service doesn't
                                       come back up healthy.
POST /api/system/tls/regenerate     — issue a fresh self-signed pair.
GET  /api/system/tls/restart-status — poll the outcome of the last restart
                                       triggered by the two endpoints above.
"""
import ipaddress
import json
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..models import TlsRegenerateRequest, TlsUploadRequest
from ..update import RUN_UPDATE_SCRIPT, TLS_RESTART_STATUS_FILE, VERSION_FILE

router = APIRouter(tags=["system"])

TLS_DIR      = Path("/etc/spud-router/tls")
TLS_CERT     = TLS_DIR / "server.crt"
TLS_KEY      = TLS_DIR / "server.key"
TLS_CERT_BAK = TLS_DIR / "server.crt.bak"
TLS_KEY_BAK  = TLS_DIR / "server.key.bak"


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


# ── TLS certificate management ───────────────────────────────────────────────
# /etc/spud-router/tls/ is owned by the spud-router service user (install.sh
# chowns the whole SPUD_CONF tree to it), so these writes need no sudo — only
# restarting the service itself does.

def _openssl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["openssl", *args], capture_output=True, text=True)


def _looks_like_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _cert_info(cert_path: Path) -> dict:
    """Parse a cert file's subject/issuer/SAN/expiry/fingerprint. Raises
    HTTPException if the file is missing or unparseable."""
    if not cert_path.exists():
        raise HTTPException(status_code=404, detail=f"{cert_path} not found")

    fields = {}
    for flag, key in (("-subject", "subject"), ("-issuer", "issuer"), ("-enddate", "enddate")):
        proc = _openssl("x509", "-noout", flag, "-in", str(cert_path))
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Could not parse {cert_path}: {proc.stderr.strip()}")
        fields[key] = proc.stdout.strip().split("=", 1)[-1] if "=" in proc.stdout else proc.stdout.strip()

    fp_proc = _openssl("x509", "-noout", "-fingerprint", "-sha256", "-in", str(cert_path))
    fingerprint = fp_proc.stdout.strip().split("=", 1)[-1] if "=" in fp_proc.stdout else ""

    san_proc = _openssl("x509", "-noout", "-ext", "subjectAltName", "-in", str(cert_path))
    san = [line.strip() for line in san_proc.stdout.splitlines() if line.strip() and "subjectAltName" not in line]

    expired = _openssl("x509", "-noout", "-checkend", "0", "-in", str(cert_path)).returncode != 0

    return {
        "subject":            fields.get("subject", ""),
        "issuer":             fields.get("issuer", ""),
        "not_after":          fields.get("enddate", ""),
        "expired":            expired,
        "fingerprint_sha256": fingerprint,
        "san":                san,
    }


def _validate_pair(cert_pem: str, key_pem: str) -> None:
    """Validate a cert+key pair before anything touches disk. Raises
    HTTPException(422) with a human-readable reason on any failure — the
    live cert/key are never touched unless every check here passes."""
    with tempfile.TemporaryDirectory() as td:
        cert_tmp = Path(td) / "cert.pem"
        key_tmp  = Path(td) / "key.pem"
        cert_tmp.write_text(cert_pem)
        key_tmp.write_text(key_pem)

        if _openssl("x509", "-noout", "-in", str(cert_tmp)).returncode != 0:
            raise HTTPException(status_code=422, detail="cert_pem does not parse as a valid X.509 certificate")

        if _openssl("x509", "-noout", "-checkend", "0", "-in", str(cert_tmp)).returncode != 0:
            raise HTTPException(status_code=422, detail="Certificate is expired")

        # openssl pkey handles unencrypted RSA/EC/PKCS8 keys uniformly; an
        # encrypted (passphrase-protected) key is rejected here too, since
        # there's no way to prompt for a passphrase server-side.
        if _openssl("pkey", "-noout", "-in", str(key_tmp)).returncode != 0:
            raise HTTPException(status_code=422, detail="key_pem does not parse as a valid unencrypted private key")

        cert_pub = _openssl("x509", "-noout", "-pubkey", "-in", str(cert_tmp)).stdout
        key_pub  = _openssl("pkey", "-pubout", "-in", str(key_tmp)).stdout
        if not cert_pub or cert_pub != key_pub:
            raise HTTPException(status_code=422, detail="Private key does not match the certificate")


def _backup_and_write(cert_pem: str, key_pem: str) -> None:
    """Back up the current pair (single slot — last-known-good, restorable by
    the detached tls-restart routine), then atomically write the new one."""
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    if TLS_CERT.exists():
        TLS_CERT_BAK.write_text(TLS_CERT.read_text())
        TLS_CERT_BAK.chmod(0o644)
    if TLS_KEY.exists():
        TLS_KEY_BAK.write_text(TLS_KEY.read_text())
        TLS_KEY_BAK.chmod(0o600)

    cert_tmp = TLS_DIR / "server.crt.tmp"
    key_tmp  = TLS_DIR / "server.key.tmp"
    cert_tmp.write_text(cert_pem)
    key_tmp.write_text(key_pem)
    key_tmp.chmod(0o600)
    cert_tmp.chmod(0o644)
    cert_tmp.replace(TLS_CERT)
    key_tmp.replace(TLS_KEY)


def _restart_with_fallback() -> None:
    """
    Trigger the detached restart-with-health-check-and-rollback routine.
    Runs via `sudo run-update.sh tls-restart` → a systemd-run unit →
    update.py --tls-restart, which survives the `systemctl restart
    spud-router` it performs (same detachment pattern as reboot/update).
    """
    result = subprocess.run(
        ["sudo", str(RUN_UPDATE_SCRIPT), "tls-restart"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to schedule TLS restart: {result.stderr.strip()}",
        )


@router.get("/api/system/tls", dependencies=[Depends(require_auth)])
def get_tls():
    return _cert_info(TLS_CERT)


@router.post("/api/system/tls", dependencies=[Depends(require_auth)])
def upload_tls(req: TlsUploadRequest):
    """Validate first (live cert untouched on any failure), then back up +
    write + restart. The restart drops this request's own TLS session —
    that's unavoidable and expected; the client should reconnect and poll
    /api/system/tls/restart-status."""
    _validate_pair(req.cert_pem, req.key_pem)
    _backup_and_write(req.cert_pem, req.key_pem)
    _restart_with_fallback()
    return {"restarting": True}


@router.post("/api/system/tls/regenerate", dependencies=[Depends(require_auth)])
def regenerate_tls(req: TlsRegenerateRequest):
    san_entries = [f"IP:{s}" if _looks_like_ip(s) else f"DNS:{s}" for s in req.san]
    if "DNS:localhost" not in san_entries:
        san_entries.append("DNS:localhost")
    san_str = ",".join(san_entries)

    with tempfile.TemporaryDirectory() as td:
        cert_tmp = Path(td) / "cert.pem"
        key_tmp  = Path(td) / "key.pem"
        proc = subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(key_tmp), "-out", str(cert_tmp),
                "-days", "3650",
                "-subj", f"/CN={req.common_name}",
                "-addext", f"subjectAltName={san_str}",
            ],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Certificate generation failed: {proc.stderr.strip()}")
        cert_pem = cert_tmp.read_text()
        key_pem  = key_tmp.read_text()

    _backup_and_write(cert_pem, key_pem)
    _restart_with_fallback()
    return {"restarting": True}


@router.get("/api/system/tls/restart-status", dependencies=[Depends(require_auth)])
def tls_restart_status():
    """
    Poll the outcome of the last TLS-triggered restart. Written by
    update.py's --tls-restart routine (see TLS_RESTART_STATUS_FILE).
    Absent file means no TLS restart has happened yet this boot.
    """
    if not TLS_RESTART_STATUS_FILE.exists():
        return {"state": "none"}
    try:
        return json.loads(TLS_RESTART_STATUS_FILE.read_text())
    except (OSError, ValueError):
        return {"state": "unknown"}
