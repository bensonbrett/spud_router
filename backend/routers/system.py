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
GET  /api/system/monitor            — authed; a point-in-time snapshot of
                                       memory/load/CPU/disk/interface counters,
                                       read entirely from /proc and /sys (no
                                       subprocess calls). See the section below.
"""
import ipaddress
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..models import TlsRegenerateRequest, TlsUploadRequest
from ..state import load_state
from ..update import RUN_UPDATE_SCRIPT, TLS_RESTART_STATUS_FILE, VERSION_FILE

router = APIRouter(tags=["system"])

TLS_DIR      = Path("/etc/spud-router/tls")
TLS_CERT     = TLS_DIR / "server.crt"
TLS_KEY      = TLS_DIR / "server.key"
TLS_CERT_BAK = TLS_DIR / "server.crt.bak"
TLS_KEY_BAK  = TLS_DIR / "server.key.bak"

# ── /api/system/monitor paths ────────────────────────────────────────────────
# Module-level constants (mirroring TLS_DIR/TLS_CERT above) so tests can
# monkeypatch them to point at fixture files instead of the real /proc.
MEMINFO_PATH  = Path("/proc/meminfo")
LOADAVG_PATH  = Path("/proc/loadavg")
STAT_PATH     = Path("/proc/stat")
NET_DEV_PATH  = Path("/proc/net/dev")
DISK_PATHS    = {"root": "/", "spud_conf": "/etc/spud-router"}


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


# ── System monitoring ─────────────────────────────────────────────────────────
# Everything below is read straight from /proc and /sys — no subprocess calls,
# no external tools. Parsing is factored into small pure functions that take
# already-read file *contents* (trivially unit-testable); the try/except
# around missing/unreadable files lives at the call site, one section at a
# time, so a single absent file (or a VLAN subinterface that doesn't exist —
# e.g. in this dev sandbox) degrades that one section to None/{} instead of
# 500ing the whole endpoint. Mirrors the _sysfs()/_carrier() pattern in
# config.py's diagnostics().

def _parse_meminfo(text: str) -> dict:
    """Parse the contents of /proc/meminfo into a dict of kB values. Raises
    ValueError if the required fields aren't present."""
    raw: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        raw[key.strip()] = int(parts[0])

    if "MemTotal" not in raw or "MemFree" not in raw:
        raise ValueError("meminfo missing MemTotal/MemFree")

    total     = raw["MemTotal"]
    free      = raw["MemFree"]
    buffers   = raw.get("Buffers", 0)
    cached    = raw.get("Cached", 0)
    available = raw.get("MemAvailable")

    # MemAvailable isn't present on very old kernels — fall back to the
    # classic approximation.
    used = (total - available) if available is not None else (total - free - buffers - cached)

    return {
        "mem_total_kb":     total,
        "mem_free_kb":      free,
        "mem_available_kb": available,
        "mem_buffers_kb":   buffers,
        "mem_cached_kb":    cached,
        "mem_used_kb":      max(used, 0),
        "swap_total_kb":    raw.get("SwapTotal", 0),
        "swap_free_kb":     raw.get("SwapFree", 0),
    }


def _read_memory() -> dict | None:
    try:
        text = MEMINFO_PATH.read_text()
    except OSError:
        return None
    try:
        return _parse_meminfo(text)
    except ValueError:
        return None


def _parse_loadavg(text: str) -> dict:
    """Parse the contents of /proc/loadavg — first three fields are the
    1/5/15 minute load averages."""
    parts = text.split()
    if len(parts) < 3:
        raise ValueError("unexpected /proc/loadavg format")
    return {
        "load1":  float(parts[0]),
        "load5":  float(parts[1]),
        "load15": float(parts[2]),
    }


def _read_loadavg() -> dict | None:
    try:
        text = LOADAVG_PATH.read_text()
    except OSError:
        return None
    try:
        return _parse_loadavg(text)
    except (ValueError, IndexError):
        return None


def _parse_cpu_line(text: str) -> tuple[int, int] | None:
    """Parse the aggregate `cpu ` line of /proc/stat into (total, idle)
    jiffy counts. idle here includes iowait, matching how most CPU-percent
    tools define "idle". Returns None if the line isn't found or is
    malformed."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            fields = line.split()[1:]
            try:
                nums = [int(f) for f in fields]
            except ValueError:
                return None
            if len(nums) < 4:
                return None
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
            total = sum(nums)
            return total, idle
    return None


def _read_cpu_percent() -> float | None:
    """Sample /proc/stat twice ~0.1s apart and compute aggregate CPU
    utilization as a percentage from the delta. Returns None on any error,
    and 0.0 (rather than dividing by zero) if the two samples are identical."""
    try:
        sample1 = STAT_PATH.read_text()
    except OSError:
        return None
    parsed1 = _parse_cpu_line(sample1)
    if parsed1 is None:
        return None

    time.sleep(0.1)

    try:
        sample2 = STAT_PATH.read_text()
    except OSError:
        return None
    parsed2 = _parse_cpu_line(sample2)
    if parsed2 is None:
        return None

    total1, idle1 = parsed1
    total2, idle2 = parsed2
    total_delta = total2 - total1
    idle_delta  = idle2 - idle1
    if total_delta <= 0:
        return 0.0
    return round((1 - idle_delta / total_delta) * 100, 1)


def _disk_usage(path: str) -> dict | None:
    """
    Usage for a single mount via os.statvfs(). free_bytes uses f_bavail
    (space available to a non-root user, matching what `df` reports as
    "Avail"); used_bytes is total minus f_bfree (all free blocks, including
    ones reserved for root) so used+free-as-df-shows-it doesn't need to
    exactly equal total — same convention `df` itself uses.
    """
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    free  = st.f_bavail * st.f_frsize
    used  = total - (st.f_bfree * st.f_frsize)
    return {"total_bytes": total, "used_bytes": used, "free_bytes": free}


def _read_disks() -> dict:
    disks = {}
    for label, path in DISK_PATHS.items():
        info = _disk_usage(path)
        if info is not None:
            disks[label] = info
    return disks


def _parse_net_dev(text: str) -> dict:
    """
    Parse /proc/net/dev contents into {iface: {rx/tx counters}}. Column
    layout after the `iface:` token is fixed: 8 RX fields
    (bytes packets errs drop fifo frame compressed multicast) followed by
    8 TX fields (bytes packets errs drop fifo colls carrier compressed).
    """
    result = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        iface, _, rest = line.partition(":")
        iface = iface.strip()
        if not iface or iface == "face":  # header line: " face |bytes ..."
            continue
        try:
            nums = [int(f) for f in rest.split()]
        except ValueError:
            continue
        if len(nums) < 16:
            continue
        result[iface] = {
            "rx_bytes":   nums[0],
            "rx_packets": nums[1],
            "rx_errs":    nums[2],
            "rx_drop":    nums[3],
            "tx_bytes":   nums[8],
            "tx_packets": nums[9],
            "tx_errs":    nums[10],
            "tx_drop":    nums[11],
        }
    return result


def _read_interfaces() -> dict:
    """
    Cumulative counters for the WAN interface and every VLAN subinterface
    configured in state.json. Instantaneous — the client is expected to
    diff two polls to derive a throughput rate. An interface that doesn't
    exist yet (e.g. a VLAN subinterface not present in this sandbox) is
    simply omitted, not an error.
    """
    state      = load_state()
    router_cfg = state.get("router", {})
    vlans      = state.get("vlans", [])

    wanted = []
    wan_if = router_cfg.get("wan_interface", "")
    if wan_if:
        wanted.append(wan_if)
    for v in vlans:
        try:
            wanted.append(f"{v['interface']}.{v['vlan_id']}")
        except KeyError:
            continue

    try:
        text = NET_DEV_PATH.read_text()
    except OSError:
        return {}

    all_ifaces = _parse_net_dev(text)
    return {name: all_ifaces[name] for name in wanted if name in all_ifaces}


@router.get("/api/system/monitor", dependencies=[Depends(require_auth)])
def system_monitor():
    """
    Point-in-time system resource snapshot — memory, load average, aggregate
    CPU utilization, disk usage for / and /etc/spud-router, and per-interface
    (WAN + each VLAN) traffic counters. Everything is read from /proc/ and
    /sys directly; each section fails independently (None/{} on error)
    instead of ever 500ing the whole response.
    """
    return {
        "memory":      _read_memory(),
        "load":        _read_loadavg(),
        "cpu_percent": _read_cpu_percent(),
        "disks":       _read_disks(),
        "interfaces":  _read_interfaces(),
    }
