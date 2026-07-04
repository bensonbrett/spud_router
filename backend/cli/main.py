"""
spud-cli entry point.

Handles authentication, the main menu loop, and the Apply action.
Everything else is delegated to the tabs package.
"""
import getpass
import select
import sys
import time

from .api import GET, POST, clear_token, get_token, load_token, save_token
from .ui import (
    bold, dim, err, hi, ok, warn,
    clear, confirm, menu, pause,
    print_logo, print_status_bar, section,
)
from .tabs import dns, firewall, routes, settings, snmp, status, syslog, update, vlans, vpn, wan, wireless


from pathlib import Path

INSTALL_DIR  = Path("/opt/spud-router")
VERSION_FILE = INSTALL_DIR / "VERSION"


def _current_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return "unknown"


def _do_login() -> bool:
    """Prompt for credentials and attempt login. Returns True on success."""
    clear()
    print_logo()
    print(f"  {bold('Sign in to spud-router')}\n")
    try:
        username = _prompt_inline("Username", "admin")
        password = getpass.getpass(f"  › Password: ")
    except (KeyboardInterrupt, EOFError):
        print("\n  Goodbye.")
        sys.exit(0)

    try:
        res = POST("/api/auth/login", {"username": username, "password": password})
        save_token(res["token"])
        return True
    except RuntimeError as e:
        print(err(f"\n  Login failed: {e}"))
        pause()
        return False


def _prompt_inline(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  › {msg}{suffix} ").strip()
        return val if val else default
    except (KeyboardInterrupt, EOFError):
        return default


def ensure_auth() -> None:
    """Ensure a valid session token exists, prompting for login if needed."""
    load_token()
    if get_token():
        try:
            GET("/api/state")
            return
        except RuntimeError:
            pass  # Token expired or invalid — fall through to login
    while not _do_login():
        pass


def apply_config() -> None:
    """Write and activate all config."""
    section("Apply Config")
    print(warn("  This will write and activate:"))
    for f in [
        "  /etc/netplan/50-spud-router.yaml",
        "  /etc/dnsmasq.d/spud-router.conf",
        "  /etc/spud-router/iptables.sh",
    ]:
        print(dim(f))
    print()

    if not confirm("Apply now?"):
        return

    print()
    try:
        result = POST("/api/apply", {"dry_run": False})
        for step in result.get("steps", []):
            print(f"  {ok('✓')} {step}")
        print(ok("\n  Config applied successfully"))
    except RuntimeError as e:
        print(err(f"\n  Apply failed: {e}"))
        pause()
        return

    if result.get("armed"):
        _wait_for_apply_confirmation(result["token"], result["window_seconds"])
    else:
        pause()


def _wait_for_apply_confirmation(token: str, window_seconds: int) -> None:
    """
    Every real Apply is armed with a connectivity-watchdog auto-revert (see
    backend/apply_core.py / deploy/spud-commit.sh) — mirrors the Web UI's
    countdown banner. Pressing Enter at any point confirms early; letting
    the window expire leaves the device to auto-revert on its own.
    """
    section("Confirm Changes")
    print(warn(f"  ⏱ If not confirmed within {window_seconds}s, the device will automatically"))
    print(warn("  revert to the previous configuration (in case this change broke connectivity)."))
    print(dim("\n  Press Enter at any time to confirm and keep the changes.\n"))

    deadline   = time.time() + window_seconds
    confirmed  = False
    try:
        while time.time() < deadline:
            remaining = max(0, int(deadline - time.time()))
            print(f"\r  {hi(str(remaining))}s remaining — press Enter to keep changes...  ", end="", flush=True)
            ready, _, _ = select.select([sys.stdin], [], [], 1.0)
            if ready:
                sys.stdin.readline()
                confirmed = True
                break
    except (KeyboardInterrupt, EOFError):
        pass
    print()

    if confirmed:
        try:
            POST("/api/apply/confirm", {"token": token})
            print(ok("  ✓ Changes kept."))
        except RuntimeError as e:
            print(err(f"  Could not confirm: {e}"))
    else:
        print(warn("  ⚠ Confirmation window expired — the device will auto-revert shortly."))
    pause()


def main() -> None:
    """Main menu loop — runs until the user exits."""
    ensure_auth()

    while True:
        clear()
        print_logo()

        try:
            state = GET("/api/state")
        except RuntimeError as e:
            print(err(f"  Backend unreachable: {e}"))
            print(dim("  Is spud-router running?  systemctl status spud-router"))
            pause()
            continue

        print_status_bar(state)

        idx = menu("Main Menu", [
            ("VLANs",     f"{len(state.get('vlans',[]))} configured"),
            ("WAN",       f"{state.get('router',{}).get('wan_interface','?')} {state.get('router',{}).get('wan_mode','')}"),
            ("DNS",       f"{len(state.get('dns_entries',[]))} entries"),
            ("Routes",    f"{len(state.get('static_routes',[]))} routes"),
            ("Firewall",  f"{len(state.get('fw_inbound',[]))} inbound  {len(state.get('fw_intervlan',[]))} inter-VLAN"),
            ("VPN",       ok("enabled") if state.get("tailscale", {}).get("enabled") else dim("none active")),
            ("Wireless",  ok("enabled") if state.get("wireless", {}).get("enabled") else dim("disabled")),
            ("Syslog",    ok("enabled") if state.get("syslog", {}).get("enabled") else dim("disabled")),
            ("SNMP",      ok("enabled") if state.get("snmp", {}).get("enabled") else dim("disabled")),
            ("Status",    "Interfaces, routing table, DHCP leases"),
            ("⚡ Apply",  "Write and activate all config"),
            ("⬆ Update",  f"Installed: {_current_version()}"),
            ("Settings",  "Password · backup · preview"),
        ], back_label="Exit spud-cli")

        if idx == -1:
            if confirm("Exit spud-cli? (returns to bash)"):
                clear()
                print(dim("  Goodbye.\n"))
                sys.exit(0)
        elif idx == 0: vlans.screen(state)
        elif idx == 1: wan.screen(state)
        elif idx == 2: dns.screen(state)
        elif idx == 3: routes.screen(state)
        elif idx == 4: firewall.screen(state)
        elif idx == 5: vpn.screen(state)
        elif idx == 6: wireless.screen(state)
        elif idx == 7: syslog.screen(state)
        elif idx == 8: snmp.screen(state)
        elif idx == 9: status.screen()
        elif idx == 10: apply_config()
        elif idx == 11: update.screen()
        elif idx == 12:
            signed_out = settings.screen()
            if signed_out:
                ensure_auth()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(dim("\n\n  Interrupted. Goodbye.\n"))
        sys.exit(0)
