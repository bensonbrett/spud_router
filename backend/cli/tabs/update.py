# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Update tab — check for and apply updates from GitHub, plus device reboot."""
import time

from ..api import GET, POST
from ..ui import (
    bold, dim, err, ok, warn,
    clear, confirm, menu, pause, print_logo,
    section,
)

POLL_INTERVAL_SEC   = 1.5
OVERALL_TIMEOUT_SEC = 3 * 60
TERMINAL_STATES     = ("success", "rolledback", "failed")


def screen() -> None:
    while True:
        clear()
        print_logo()
        section("Software Update")

        idx = menu("Update", [
            ("Check for updates", ""),
            ("Reboot device",     "Restart the whole device"),
        ])
        if idx == -1:
            return
        if idx == 0:
            _check_and_apply()
        elif idx == 1:
            _reboot()


def _check_and_apply() -> None:
    print(dim("\n  Checking for updates…"))
    try:
        info = GET("/api/update/check")
    except RuntimeError as e:
        print(err(f"  Cannot check for updates: {e}"))
        pause()
        return

    if info.get("error"):
        print(err(f"  {info['error']}"))
        pause()
        return

    current    = info.get("current", "unknown")
    latest     = info.get("latest", "unknown")
    up_to_date = info.get("up_to_date", True)

    print(f"\n  {'Installed:':<16} {bold(current)}")
    print(f"  {'Latest:':<16} {bold(latest)}")

    if up_to_date:
        print(f"\n  {ok('✓ Already up to date.')}")
        pause()
        return

    # Show changelog
    changelog = info.get("changelog", "")
    if changelog:
        tag_label = info.get("tag", "")
        print(f"\n  {bold('Release notes for ' + tag_label)}:")
        for line in changelog.splitlines()[:20]:
            print(f"  {dim(line)}")
        if len(changelog.splitlines()) > 20:
            print(dim("  …(truncated)"))

    print()
    if not confirm(f"Install version {latest}?"):
        return

    print()
    try:
        POST("/api/update/apply", {})
    except RuntimeError as e:
        print(err(f"  Could not start update: {e}"))
        pause()
        return

    _poll_progress()


def _poll_progress() -> None:
    """Poll GET /api/update/status until a terminal state, printing new log
    lines as they arrive. Tolerates the backend being briefly unreachable
    while it restarts mid-update."""
    seen        = 0
    started     = time.time()
    unreachable = False

    while True:
        try:
            s = GET("/api/update/status")
            unreachable = False
            log = s.get("log", [])
            for line in log[seen:]:
                _print_log_line(line)
            seen = len(log)

            if s.get("state") in TERMINAL_STATES:
                print()
                _print_terminal(s)
                pause()
                return
        except RuntimeError:
            if not unreachable:
                print(dim("  … applying (service restarting — this is expected) …"))
                unreachable = True

        if time.time() - started > OVERALL_TIMEOUT_SEC:
            print(warn("\n  Taking longer than expected."))
            print(dim("  Check back in a minute, or check manually:"))
            print(dim("    cat /run/spud-router/update-status.json"))
            pause()
            return

        time.sleep(POLL_INTERVAL_SEC)


def _print_log_line(line: str) -> None:
    if line.startswith("ERROR") or "ERROR:" in line:
        print(f"  {err(line)}")
    elif "✓" in line or line.startswith("✓"):
        print(f"  {ok(line)}")
    elif "⚠" in line or line.startswith("WARNING"):
        print(f"  {warn(line)}")
    else:
        print(f"  {dim(line)}")


def _print_terminal(s: dict) -> None:
    state = s.get("state")
    if state == "success":
        version = s.get("installed_version") or s.get("to_version", "")
        print(ok(f"  ✓ Update complete — now running v{version}, confirmed healthy."))
    elif state == "rolledback":
        print(warn(f"  {s.get('message', 'Update failed and was rolled back.')}"))
        print(dim("  No action needed — the device is running the previous version."))
    elif state == "failed":
        print(err(f"  {s.get('message', 'Update failed.')}"))
        print(dim("  Check manually over SSH: sudo python3 /opt/spud-router/update.py"))


def _reboot() -> None:
    print()
    print(warn("  ⚠ This reboots the device. It will be unreachable for ~1–2 minutes."))
    print(warn("  If you're remote, make sure you have another way back in (Tailscale SSH)."))
    if not confirm("Confirm reboot now?"):
        return
    try:
        POST("/api/system/reboot")
        print(ok("\n  ✓ Rebooting… reconnect in a minute."))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()
