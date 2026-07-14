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
            ("AI agent setup",   "Connect AI agents to your router via MCP"),
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

    scopes = _select_scopes()
    if not scopes:
        print(err("  At least one scope is required."))
        pause()
        return

    expires_at = _select_expiry()

    try:
        body = {"name": name, "scopes": scopes}
        if expires_at is not None:
            body["expires_at"] = expires_at
        result = POST("/api/api-keys", body)
        section("API Key Created")
        print(warn("  ⚠ Copy this key now — it will never be shown again:"))
        print()
        print(f"  \033[32m{result['key']}\033[0m")
        print()
        print(f"  ID:     {result['id']}")
        print(f"  Scopes: {','.join(result['scopes'])}")
        print(f"  Expires: {'never' if not result.get('expires_at') else time.strftime('%Y-%m-%d', time.localtime(result['expires_at']))}")
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


_ALL_SCOPES = [
    ("read",        "Read-only access"),
    ("write",       "Read + write (no apply)"),
    ("apply",       "Read + write + apply (full config)"),
    ("diagnostics", "Read + diagnostics"),
    ("vpn",         "VPN management"),
]


def _select_scopes() -> list[str]:
    """Multi-select scope picker — the model accepts a list, so unlike a
    single-choice menu(), let the admin toggle any combination on."""
    selected: list[str] = []
    while True:
        print()
        for i, (name, desc) in enumerate(_ALL_SCOPES, 1):
            mark = ok("[x]") if name in selected else dim("[ ]")
            print(f"  {i}. {mark} {name} — {desc}")
        print(dim("\n  Enter a number to toggle, or Enter to finish"))
        try:
            val = prompt("").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not val:
            break
        try:
            i = int(val) - 1
            if 0 <= i < len(_ALL_SCOPES):
                name = _ALL_SCOPES[i][0]
                if name in selected:
                    selected.remove(name)
                else:
                    selected.append(name)
            else:
                print(err("  Invalid number"))
        except ValueError:
            print(err("  Enter a number, or Enter to finish"))
    return selected


def _select_expiry() -> int | None:
    """Optional key expiration — the model supports it but the CLI never let
    admins set it, so every CLI-created key was non-expiring."""
    idx = menu("Expires", [
        ("Never",    ""),
        ("30 days",  ""),
        ("90 days",  ""),
        ("1 year",   ""),
    ], back_label="Never")
    days = {1: 30, 2: 90, 3: 365}.get(idx)
    if not days:
        return None
    return int(time.time()) + days * 86400


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
    """AI agent setup submenu."""
    while True:
        section("AI Agent Setup")
        try:
            status = GET("/api/mcp/status")
            config = GET("/api/mcp/config")
        except RuntimeError as e:
            print(err(f"  {e}"))
            pause()
            return

        if status.get("configured"):
            print(f"  {ok('  ✓ API key configured')}")
            print(f"  Key ID: {config.get('api_key_id', '')}")
            print()
            print(dim("  Install on your machine:"))
            print(dim("  pip install git+https://github.com/bensonbrett/spud_router.git"))
            print()
            print(dim("  Then run the MCP server locally:"))
            print("  \033[32mspud-router-mcp --api-key <your-key> --base-url https://192.168.10.1:8080\033[0m")
            print()
            print(dim("  Or add to OpenCode / Claude Desktop / Copilot config:"))
            print(dim('  {"command": "spud-router-mcp", "args": ["--api-key", "<key>", "--base-url", "https://..." ]}'))
        else:
            print(f"  {warn('  Not configured — generate an API key to get started')}")

        actions = [("Generate API Key", "Auto-generate key and show setup instructions")]
        if status.get("configured"):
            actions.append(("Disable MCP", "Clear the configured API key"))
        actions.append(("Back", ""))
        idx = menu("AI Agent Actions", actions, back_label="Back")
        if idx in (-1, len(actions) - 1):
            return
        if idx == 0:
            _mcp_enable()
        elif actions[idx][0] == "Disable MCP":
            _mcp_disable()


def _mcp_enable() -> None:
    section("Generate API Key")
    if not confirm("This will create an API key for AI agent access. Continue?"):
        return

    try:
        result = POST("/api/mcp/enable")
        section("API Key Created — Copy It Now")
        print(warn("  ⚠ This key will never be shown again:"))
        print()
        print(f"  \033[32m{result['key']}\033[0m")
        print()
        print(dim("  Install the CLI on your machine:"))
        print("  pip install git+https://github.com/bensonbrett/spud_router.git")
        print()
        print(dim("  Then connect to your router:"))
        print(f"  spud-router-mcp --api-key {result['key']} --base-url https://192.168.10.1:8080")
        print()
        print(dim("  Or add this to OpenCode / Claude Desktop / Copilot config:"))
        print(dim(f'  {{"command": "spud-router-mcp", "args": ["--api-key", "{result["key"]}", "--base-url", "https://192.168.10.1:8080"]}}'))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _mcp_disable() -> None:
    section("Disable MCP")
    if not confirm("Clear the MCP API key configuration? AI agents will lose access."):
        return
    try:
        DELETE("/api/mcp/config")
        print(ok("\n  ✓ MCP configuration cleared"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
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
