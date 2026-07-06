# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Settings tab — password, backup, preview, TLS certificate, sign out."""
import getpass
import json
import os
import time

from ..api import DELETE, GET, POST, clear_token
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
            ("API keys",         "Manage API keys for programmatic access"),
            ("MCP server",       "Model Context Protocol server for AI agents"),
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
            _api_keys()
        elif idx == 6:
            _mcp()
        elif idx == 7:
            return _sign_out()


def _api_keys() -> None:
    """Manage API keys submenu."""
    while True:
        section("API Keys")
        try:
            keys = GET("/api/api-keys")
        except RuntimeError as e:
            print(err(f"  {e}"))
            pause()
            return

        if keys:
            print(f"  {'Name':<20} {'Scope':<20} {'Created':<25} {'Last Used'}")
            print(f"  {'─'*86}")
            for k in keys:
                created = time.strftime("%Y-%m-%d %H:%M", time.localtime(k.get("created_at") or 0))
                last_used = time.strftime("%Y-%m-%d %H:%M", time.localtime(k.get("last_used") or 0)) if k.get("last_used") else "Never"
                print(f"  {k['name']:<20} {','.join(k['scopes']):<20} {created:<25} {last_used}")
        else:
            print(dim("  No API keys yet."))

        idx = menu("API Key Actions", [
            ("Create new key",  ""),
            ("Revoke key",      ""),
            ("Back",           ""),
        ], back_label="Back")
        if idx in (-1, 2):
            return
        if idx == 0:
            _api_key_create()
        elif idx == 1:
            _api_key_revoke(keys)


def _api_key_create() -> None:
    section("Create API Key")
    try:
        name = prompt("Key name", "MCP server")
        if not name:
            print(err("  Name is required."))
            pause()
            return
    except (KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        pause()
        return

    idx = menu("Select scope", [
        ("read",       "Read-only access"),
        ("write",      "Read + write (no apply)"),
        ("apply",      "Read + write + apply (full config)"),
        ("diagnostics","Read + diagnostics"),
        ("vpn",        "VPN management"),
    ])
    if idx == -1:
        return
    scope = ["read", "write", "apply", "diagnostics", "vpn"][idx]

    try:
        result = POST("/api/api-keys", {"name": name, "scopes": [scope]})
        section("API Key Created")
        print(warn("  ⚠ Copy this key now — it will never be shown again:"))
        print()
        print(f"  \033[32m{result['key']}\033[0m")
        print()
        print(f"  ID:     {result['id']}")
        print(f"  Scope:  {','.join(result['scopes'])}")
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _api_key_revoke(keys: list) -> None:
    if not keys:
        print(err("  No keys to revoke."))
        pause()
        return

    section("Revoke API Key")
    options = [(k["name"], k["id"]) for k in keys]
    idx = menu("Select key to revoke", options)
    if idx == -1:
        return

    key_id = keys[idx]["id"]
    key_name = keys[idx]["name"]
    if not confirm(f"Revoke '{key_name}'? This cannot be undone."):
        return

    try:
        DELETE(f"/api/api-keys/{key_id}")
        print(ok(f"  ✓ Key '{key_name}' revoked"))
    except RuntimeError as e:
        print(err(f"  Error: {e}"))
    pause()


def _mcp() -> None:
    """MCP server management submenu."""
    while True:
        section("MCP Server")
        try:
            status = GET("/api/mcp/status")
            config = GET("/api/mcp/config")
        except RuntimeError as e:
            print(err(f"  {e}"))
            pause()
            return

        print(f"  {'Status:':<14} {dim('●') if status.get('running') else '○'} {'Running' if status.get('running') else 'Stopped'}")
        if status.get("configured"):
            print(f"  {'Mode:':<14} {config.get('read_only', False) and 'Read-only' or 'Read-write'}")
            if config.get("base_url"):
                print(f"  {'URL:':<14} {config.get('base_url')}")
            if config.get("api_key_id"):
                print(f"  {'Key:':<14} {config.get('api_key_id')}…")
        else:
            print(f"  {warn('  Not configured')}")

        idx = menu("MCP Actions", [
            ("Configure",    ""),
            ("Start",        ""),
            ("Stop",         ""),
            ("Back",         ""),
        ], back_label="Back")
        if idx in (-1, 3):
            return
        if idx == 0:
            _mcp_configure()
        elif idx == 1:
            _mcp_start()
        elif idx == 2:
            _mcp_stop()


def _mcp_configure() -> None:
    section("Configure MCP Server")
    try:
        api_key = getpass.getpass(f"  › API Key (spud_...): ")
        if not api_key:
            print(err("  API key is required."))
            pause()
            return
        if not api_key.startswith("spud_"):
            print(err("  API key must start with 'spud_'."))
            pause()
            return
    except (KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        pause()
        return

    try:
        base_url = prompt("Backend URL", "https://127.0.0.1:8080")
    except (KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        confirm_raw = prompt("Confirm window (seconds)", "120")
        confirm_window = int(confirm_raw) if confirm_raw else 120
    except (KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    tls_verify = confirm("TLS verify", False)
    read_only = confirm("Read-only mode", False)

    try:
        POST("/api/mcp/config", {
            "api_key": api_key,
            "base_url": base_url,
            "tls_verify": tls_verify,
            "read_only": read_only,
            "confirm_window_seconds": confirm_window,
        })
        print(ok("\n  ✓ MCP configuration saved"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _mcp_start() -> None:
    try:
        POST("/api/mcp/start")
        print(ok("  ✓ MCP server started"))
    except RuntimeError as e:
        print(err(f"  Error: {e}"))
    pause()


def _mcp_stop() -> None:
    try:
        POST("/api/mcp/stop")
        print(ok("  ✓ MCP server stopped"))
    except RuntimeError as e:
        print(err(f"  Error: {e}"))
    pause()


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
