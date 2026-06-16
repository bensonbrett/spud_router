"""Update tab — check for and apply updates from GitHub."""
import json
import subprocess
from pathlib import Path

from ..api import GET, POST
from ..ui import (
    bold, dim, err, hi, ok, warn,
    clear, confirm, pause, print_logo,
    print_status_bar, section,
)

INSTALL_DIR  = Path("/opt/spud-router")
VERSION_FILE = INSTALL_DIR / "VERSION"
UPDATE_SCRIPT = INSTALL_DIR / "update.py"


def screen() -> None:
    clear()
    print_logo()
    section("Software Update")

    # Check for update
    print(dim("  Checking for updates…"))
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

    current  = info.get("current", "unknown")
    latest   = info.get("latest", "unknown")
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

    # Run the updater and stream output
    print()
    if not UPDATE_SCRIPT.exists():
        print(err(f"  Update script not found at {UPDATE_SCRIPT}"))
        print(dim("  Re-run the installer to fix this."))
        pause()
        return

    try:
        proc = subprocess.Popen(
            ["python3", str(UPDATE_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip("\n")
            if line.startswith("ERROR") or "ERROR:" in line:
                print(f"  {err(line)}")
            elif "✓" in line or line.startswith("✓"):
                print(f"  {ok(line)}")
            elif "⚠" in line or line.startswith("WARNING"):
                print(f"  {warn(line)}")
            else:
                print(f"  {dim(line)}")

        proc.wait()
        print()
        if proc.returncode == 0:
            print(ok(f"  Update to {latest} complete."))
            print(dim("  The service has been restarted."))
        elif proc.returncode == 2:
            print(ok("  Already up to date."))
        else:
            print(err("  Update failed."))
            print(dim("  Check the output above for details."))

    except Exception as e:
        print(err(f"  Error running updater: {e}"))

    pause()
