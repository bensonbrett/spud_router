# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
WireGuard apply logic — dependency-free (see tailscale_apply.py's
docstring for why: importable by update.py's detached revert path under
the system python3, which has no pip packages installed).

wg0.conf itself is written by apply_core.py (alongside the other generated
config files); this module only handles the imperative
enable/restart/stop/disable of the wg-quick@wg0 systemd unit, exactly
mirroring tailscale_apply.py's shape so VPN_PROVIDERS in apply_core.py can
treat every provider identically.
"""
import subprocess

from .priv import cmd as _cmd


def apply(state: dict, sudo: bool = True) -> list[str]:
    """
    Bring wg-quick@wg0 up or down to match state["wireguard"]["enabled"].
    `systemctl enable --now` gives boot persistence (survives a remote
    reboot) exactly like Tailscale's own tailscaled unit already does.
    """
    wg = state.get("wireguard", {})

    if not wg.get("enabled"):
        subprocess.run(_cmd(sudo, "systemctl", "disable", "--now", "wg-quick@wg0"), check=False, capture_output=True, text=True)
        return ["WireGuard: down"]

    subprocess.run(_cmd(sudo, "systemctl", "enable", "--now", "wg-quick@wg0"), check=False, capture_output=True, text=True)
    result = subprocess.run(_cmd(sudo, "systemctl", "restart", "wg-quick@wg0"), capture_output=True, text=True)
    if result.returncode == 0:
        return ["WireGuard: up"]
    return [f"WireGuard warning: {result.stderr.strip()}"]
