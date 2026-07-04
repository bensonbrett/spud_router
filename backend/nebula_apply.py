# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Nebula apply logic — dependency-free (see tailscale_apply.py's docstring
for why: importable by update.py's detached revert path under the system
python3, which has no pip packages installed).

config.yaml and the cert/key/CA files are written by apply_core.py
(alongside the other generated config files); this module only handles
the imperative enable/restart/stop/disable of the nebula systemd unit,
mirroring wireguard_apply.py's shape so VPN_PROVIDERS in apply_core.py
can treat every provider identically.
"""
import subprocess

from .priv import cmd as _cmd


def apply(state: dict, sudo: bool = True) -> list[str]:
    """
    Bring the nebula unit up or down to match state["nebula"]["enabled"].
    `systemctl enable --now` gives boot persistence (survives a remote
    reboot) exactly like Tailscale/WireGuard's own units already do.

    Enabled-but-credential-incomplete is treated as "down, with a
    warning" rather than an error — a half-configured Nebula must never
    block the other VPN providers (see apply_core.py's per-provider
    failure isolation), and there is nothing meaningful to start without
    a cert/key/CA triple.
    """
    nb = state.get("nebula", {})
    has_credentials = bool(nb.get("cert_pem") and nb.get("key_pem") and nb.get("ca_pem"))

    if not nb.get("enabled") or not has_credentials:
        subprocess.run(_cmd(sudo, "systemctl", "disable", "--now", "nebula"), check=False, capture_output=True, text=True)
        if nb.get("enabled") and not has_credentials:
            return ["⚠ Nebula enabled but credentials are incomplete — import a cert/key/CA to start it"]
        return ["Nebula: down"]

    subprocess.run(_cmd(sudo, "systemctl", "enable", "--now", "nebula"), check=False, capture_output=True, text=True)
    result = subprocess.run(_cmd(sudo, "systemctl", "restart", "nebula"), capture_output=True, text=True)
    if result.returncode == 0:
        return ["Nebula: up"]
    return [f"Nebula warning: {result.stderr.strip()}"]
