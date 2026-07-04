# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Settings tab — password, backup, preview, TLS certificate, sign out."""
import getpass
import json
import os
import time

from ..api import GET, POST, clear_token
from ..ui import (
    bold, dim, err, ok, warn,
    clear, confirm, menu, multiline_prompt, pause, print_logo,
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
            ("Change password",   ""),
            ("Export config",     "Save state to a JSON file"),
            ("Import config",     "Restore from a JSON backup"),
            ("Preview configs",   "View generated netplan / dnsmasq / iptables"),
            ("TLS certificate",   "View / upload / regenerate"),
            ("Sign out",          "Clear local session token"),
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
            _tls()
        elif idx == 5:
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


# ── TLS certificate ─────────────────────────────────────────────────────────

def _tls() -> None:
    while True:
        section("TLS Certificate")
        try:
            info = GET("/api/system/tls")
            print(f"  {'Subject:':<14} {info.get('subject','')}")
            print(f"  {'Issuer:':<14} {info.get('issuer','')}")
            expired_str = err(" (EXPIRED)") if info.get("expired") else ""
            print(f"  {'Expires:':<14} {info.get('not_after','')}{expired_str}")
            if info.get("san"):
                print(f"  {'SAN:':<14} {', '.join(info['san'])}")
            print(f"  {'SHA-256 fp:':<14} {info.get('fingerprint_sha256','')}")
        except RuntimeError as e:
            print(err(f"  {e}"))

        idx = menu("TLS Actions", [
            ("Upload cert + key (PEM paste)", ""),
            ("Regenerate self-signed",        ""),
            ("Back",                          ""),
        ], back_label="Back")
        if idx in (-1, 2):
            return
        if idx == 0:
            _tls_upload()
        elif idx == 1:
            _tls_regenerate()


def _tls_upload() -> None:
    section("Upload TLS Certificate + Key")
    print(warn("  ⚠ The service will restart to activate the new certificate — this session"))
    print(warn("  will briefly disconnect. If the new pair fails to come up, the previous"))
    print(warn("  one is restored automatically."))
    cert_pem = multiline_prompt("Paste the certificate (PEM)")
    if not cert_pem:
        print(err("  Cancelled."))
        pause()
        return
    key_pem = multiline_prompt("Paste the private key (PEM)")
    if not key_pem:
        print(err("  Cancelled."))
        pause()
        return
    if not confirm("Upload and restart now?"):
        return

    try:
        POST("/api/system/tls", {"cert_pem": cert_pem, "key_pem": key_pem})
        print(ok("\n  ✓ Uploaded — restarting…"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
        pause()
        return
    _wait_for_restart()


def _tls_regenerate() -> None:
    section("Regenerate Self-Signed Certificate")
    try:
        cn = prompt("Common name", "spud-router")
        san_raw = prompt("Extra SANs (comma-separated IPs/hostnames, blank for none)")
    except (KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return
    san = [s.strip() for s in san_raw.split(",") if s.strip()]
    if not confirm("Generate and restart now?"):
        return

    try:
        POST("/api/system/tls/regenerate", {"common_name": cn, "san": san})
        print(ok("\n  ✓ Generated — restarting…"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
        pause()
        return
    _wait_for_restart()


def _wait_for_restart(timeout_s: int = 60) -> None:
    """Poll /api/system/tls/restart-status until it settles, printing the
    outcome. The connection drops mid-poll (service restarting) — that's
    expected; GET() raising RuntimeError just means "keep waiting"."""
    print(dim("\n  Waiting for the service to come back…"))
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(2)
        try:
            status = GET("/api/system/tls/restart-status")
        except RuntimeError:
            continue
        state = status.get("state")
        if state == "ok":
            print(ok(f"\n  ✓ {status.get('message', 'New certificate is live.')}"))
            pause()
            return
        if state in ("rolledback", "failed"):
            print(err(f"\n  ⚠ {status.get('message', 'Restart did not succeed.')}"))
            pause()
            return
    print(warn("\n  ⚠ Timed out waiting for the restart — check connectivity and retry the TLS menu."))
    pause()
