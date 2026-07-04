# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Diagnostics commands: ping, traceroute, nslookup — run from the router itself.
Also: Wake-on-LAN, which sends a magic packet rather than shelling out.

Security: DiagnosticRequest.target is validated by models.py (strict hostname
regex or ipaddress.ip_address) before it ever reaches subprocess.run(), and
the argument list passed to subprocess.run() is always a fixed list — never
shell=True, never string-interpolated. This is the only thing standing
between this endpoint and command injection.

Wake-on-LAN needs no subprocess at all: the magic packet is built and sent
with stdlib socket only (no `wakeonlan` pip package, no new runtime deps).
WolRequest.mac is validated and normalized by models.py before it ever
reaches wake_on_lan() below.
"""
import ipaddress
import shutil
import socket
import subprocess

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..models import DiagnosticRequest, WolRequest
from ..state import load_state

router = APIRouter(tags=["diagnostics"], dependencies=[Depends(require_auth)])

TIMEOUT_SECONDS = 15
MAX_OUTPUT_BYTES = 16 * 1024

WOL_PORT = 9                                 # conventional discard-port target for magic packets
WOL_DEFAULT_BROADCAST = "255.255.255.255"    # used when no vlan_id/broadcast override is given


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


def _wol_broadcast_address(req: WolRequest) -> str:
    """
    Resolve the broadcast address a magic packet should be sent to.

    - vlan_id given: resolve that VLAN's own broadcast address, so the
      packet actually reaches that L2 segment (a plain 255.255.255.255
      broadcast doesn't cross VLAN boundaries on this router — it's only
      ever forwarded onto whichever interface the socket sends it out on).
    - broadcast given (and vlan_id is not — WolRequest forbids both):
      use the caller's explicit override as-is.
    - neither given: fall back to the global broadcast address.
    """
    if req.vlan_id is not None:
        vlan = next(
            (v for v in load_state().get("vlans", []) if v.get("vlan_id") == req.vlan_id),
            None,
        )
        if vlan is None or not vlan.get("ip_address"):
            raise HTTPException(
                status_code=400,
                detail=f"VLAN {req.vlan_id} not found or has no IP address configured",
            )
        network = ipaddress.IPv4Network(f"{vlan['ip_address']}/{vlan['prefix_len']}", strict=False)
        return str(network.broadcast_address)
    if req.broadcast:
        return req.broadcast
    return WOL_DEFAULT_BROADCAST


@router.post("/api/diagnostics/wol")
def wake_on_lan(req: WolRequest):
    """
    Send a Wake-on-LAN magic packet: 6 bytes of 0xFF followed by the target
    MAC repeated 16 times, over a broadcast UDP datagram — built and sent
    with stdlib socket only, never shelling out.
    """
    broadcast = _wol_broadcast_address(req)

    mac_bytes = bytes.fromhex(req.mac.replace(":", ""))
    payload = b"\xff" * 6 + mac_bytes * 16

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(payload, (broadcast, WOL_PORT))
    except OSError as e:
        return {"sent": False, "mac": req.mac, "broadcast": broadcast, "error": str(e)}

    return {"sent": True, "mac": req.mac, "broadcast": broadcast}
