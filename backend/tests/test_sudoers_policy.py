# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Guards that deploy/sudoers grants the privileged commands apply_core issues
as the unprivileged spud-router service user (sudo=True). Regression test for
the v0.12.0 bug where #184 split sysctls out of the iptables apply script into
apply_core._activate_sysctl() (`sudo tee /etc/sysctl.d/99-spud-router.conf` +
`sudo sysctl --system`) but never added the matching NOPASSWD grants — so every
real POST /api/apply failed at the sysctl step even though the mocked unit tests
passed. See issue #252 / #184.
"""
from pathlib import Path

import backend.apply_core as apply_core

SUDOERS = Path(__file__).resolve().parents[2] / "deploy" / "sudoers"


def _sudoers_text() -> str:
    return SUDOERS.read_text()


class TestSysctlGrants:
    def test_tee_grant_for_sysctl_conf_present(self):
        """_activate_sysctl runs `sudo tee <SYSCTL_CONF>`; the service user
        can only do that if deploy/sudoers grants exactly that path."""
        grant = f"/usr/bin/tee {apply_core.SYSCTL_CONF}"
        assert grant in _sudoers_text(), (
            f"deploy/sudoers is missing a NOPASSWD grant for '{grant}' — "
            "POST /api/apply will fail at the sysctl step (#252)."
        )

    def test_sysctl_system_grant_present(self):
        """_activate_sysctl runs `sudo sysctl --system` to apply the drop-in
        live; that exact command must be granted."""
        assert "/usr/sbin/sysctl --system" in _sudoers_text(), (
            "deploy/sudoers is missing a NOPASSWD grant for 'sysctl --system' "
            "— POST /api/apply will fail applying sysctls live (#252)."
        )
