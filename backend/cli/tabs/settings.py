"""Settings tab — password, backup, preview, sign out."""
import getpass
import json
import os

from ..api import GET, POST, clear_token
from ..ui import (
    bold, dim, err, ok,
    clear, menu, pause, print_logo,
    prompt, section,
)


def screen() -> bool:
    """
    Returns True if the user signed out (caller should re-authenticate),
    False otherwise.
    """
    while True:
        clear()
        print_logo()
        section("Settings")

        idx = menu("Settings", [
            ("Change password", ""),
            ("Export config",   "Save state to a JSON file"),
            ("Import config",   "Restore from a JSON backup"),
            ("Preview configs", "View generated netplan / dnsmasq / iptables"),
            ("Sign out",        "Clear local session token"),
        ])
        if idx == -1:
            return False
        if idx == 0:
            _change_password()
        elif idx == 1:
            _export()
        elif idx == 2:
            _import()
        elif idx == 3:
            _preview()
        elif idx == 4:
            return _sign_out()


def _change_password() -> None:
    section("Change Password")
    try:
        cur  = getpass.getpass(f"  › Current password: ")
        new  = getpass.getpass(f"  › New password (min 8 chars): ")
        if len(new) < 8:
            print(err("  Password must be at least 8 characters."))
            pause()
            return
        new2 = getpass.getpass(f"  › Confirm new password: ")
        if new != new2:
            print(err("  Passwords don't match."))
            pause()
            return
    except (KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        POST("/api/auth/change-password", {
            "current_password": cur,
            "new_password":     new,
        })
        print(ok("\n  ✓ Password changed"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _export() -> None:
    section("Export Config")
    path = prompt("Save to path", "/tmp/spud-router-backup.json")
    try:
        state = GET("/api/state")
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
        print(ok(f"\n  ✓ Saved to {path}"))
        print(dim(f"  Transfer with: scp root@<device>:{path} ./"))
    except (RuntimeError, OSError) as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _import() -> None:
    section("Import Config")
    path = prompt("Path to JSON backup file")
    if not path or not os.path.exists(path):
        print(err("  File not found."))
        pause()
        return

    try:
        with open(path) as f:
            data = json.load(f)
        result = POST("/api/config/import", data)
        print(ok(
            f"\n  ✓ Imported: {result.get('vlans',0)} VLANs  "
            f"{result.get('dns',0)} DNS  "
            f"{result.get('routes',0)} routes"
        ))
        print(dim("  Run Apply to push live."))
    except (RuntimeError, OSError, json.JSONDecodeError) as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _preview() -> None:
    section("Config Preview")
    try:
        preview = GET("/api/preview")
    except RuntimeError as e:
        print(err(f"  {e}"))
        pause()
        return

    idx = menu("View config file", [
        ("netplan",  "/etc/netplan/50-spud-router.yaml"),
        ("dnsmasq",  "/etc/dnsmasq.d/spud-router.conf"),
        ("iptables", "/etc/spud-router/iptables.sh"),
    ])
    if idx == -1:
        return

    key     = ["netplan", "dnsmasq", "iptables"][idx]
    content = preview.get(key, "")
    print()
    print("─" * 60)
    for line in content.split("\n"):
        print(f"  \033[32m{line}\033[0m")
    print("─" * 60)
    pause()


def _sign_out() -> bool:
    try:
        POST("/api/auth/logout")
    except RuntimeError:
        pass
    clear_token()
    print(ok("  ✓ Signed out"))
    pause()
    return True
