# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Tiny shared helper for conditionally sudo-prefixing subprocess argument
lists. Split out into its own dependency-free module (stdlib only, no
fastapi/pydantic) so it can be imported both by the FastAPI app and by
update.py, which runs under the system python3 with no pip packages
installed.
"""


def cmd(use_sudo: bool, *args: str) -> list[str]:
    """
    Build a subprocess argument list, prefixed with "sudo" when use_sudo is
    True. Pass use_sudo=True when running as the unprivileged spud-router
    service user (root-owned writes/restarts go through the NOPASSWD grants
    in deploy/sudoers); pass use_sudo=False when the caller is already root
    (e.g. the detached commit-confirm revert path) so it writes/restarts
    directly, exactly like update.py's own bare `systemctl restart
    spud-router` calls.
    """
    return (["sudo", *args]) if use_sudo else list(args)
