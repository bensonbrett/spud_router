# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Tailscale apply logic — split out of backend/routers/tailscale.py (which
also defines FastAPI routes and therefore imports fastapi) so it can be
imported by backend/apply_core.py and, transitively, by update.py's
detached revert path, which runs under the system python3 with no pip
packages installed. Keep this module dependency-free beyond the stdlib
and backend.state/backend.priv (both themselves dependency-free).
"""
import subprocess

from .priv import cmd as _cmd
from .state import TAILSCALE_AUTHKEY_FILE


def has_authkey() -> bool:
    return TAILSCALE_AUTHKEY_FILE.exists() and TAILSCALE_AUTHKEY_FILE.read_text().strip() != ""


def apply(state: dict, sudo: bool = True) -> list[str]:
    """
    Apply tailscale configuration by calling the tailscale CLI.
    Returns a list of result strings for the apply log.
    """
    ts = state.get("tailscale", {})

    if not ts.get("enabled"):
        subprocess.run(_cmd(sudo, "tailscale", "down"), check=False)
        return ["Tailscale: down"]

    tailscale_cmd = _cmd(sudo, "tailscale", "up")
    # The router runs its own dnsmasq and resolves upstream itself, so it must
    # never accept the tailnet's DNS. Without this, tailscaled overwrites
    # /etc/resolv.conf and installs the tailnet's MagicDNS resolver
    # (100.100.100.100) as the system's global resolver. On a tailnet that has
    # split-DNS routes but no global fallback resolver, that resolver SERVFAILs
    # every public name — breaking the router's own DNS (update checks, NTP,
    # etc.) and, because dnsmasq forwards upstream to it, every LAN client too.
    tailscale_cmd.append("--accept-dns=false")
    if has_authkey():
        tailscale_cmd.append(f"--auth-key=file:{TAILSCALE_AUTHKEY_FILE}")
    if ts.get("accept_routes"):
        tailscale_cmd.append("--accept-routes")
    if ts.get("advertise_routes"):
        tailscale_cmd.append("--advertise-routes=" + ",".join(ts["advertise_routes"]))
    if ts.get("exit_node"):
        tailscale_cmd.append("--advertise-exit-node")

    result = subprocess.run(tailscale_cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return ["Tailscale: up"]
    return [f"Tailscale warning: {result.stderr.strip()}"]
