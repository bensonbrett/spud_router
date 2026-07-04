# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Diagnostics commands: ping, traceroute, nslookup — run from the router itself.

Security: DiagnosticRequest.target is validated by models.py (strict hostname
regex or ipaddress.ip_address) before it ever reaches subprocess.run(), and
the argument list passed to subprocess.run() is always a fixed list — never
shell=True, never string-interpolated. This is the only thing standing
between this endpoint and command injection.
"""
import shutil
import subprocess

from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..models import DiagnosticRequest

router = APIRouter(tags=["diagnostics"], dependencies=[Depends(require_auth)])

TIMEOUT_SECONDS = 15
MAX_OUTPUT_BYTES = 16 * 1024


def _command_args(command: str, target: str) -> list[str]:
    if command == "ping":
        return ["ping", "-c", "4", "-w", "10", target]
    if command == "traceroute":
        binary = "traceroute" if shutil.which("traceroute") else "tracepath"
        if binary == "traceroute":
            return ["traceroute", "-w", "2", "-q", "1", "-m", "20", target]
        return ["tracepath", target]
    # nslookup
    return ["nslookup", target]


@router.post("/api/diagnostics/run")
def run_diagnostic(req: DiagnosticRequest):
    """Run a whitelisted diagnostic command against a validated target."""
    args = _command_args(req.command, req.target)

    timed_out = False
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        output = stdout + stderr
        exit_code = None
    except FileNotFoundError:
        output = f"{args[0]}: command not found on this router"
        exit_code = None

    truncated = len(output) > MAX_OUTPUT_BYTES
    if truncated:
        output = output[:MAX_OUTPUT_BYTES]

    return {
        "command":    req.command,
        "target":     req.target,
        "exit_code":  exit_code,
        "output":     output,
        "truncated":  truncated,
        "timed_out":  timed_out,
    }
