"""WAN and management interface tab."""
from ..api import GET, POST
from ..ui import (
    bold, dim, err, hi, ok, warn,
    clear, confirm, menu, pause, print_logo,
    print_status_bar, prompt, section, table,
)


def screen(state: dict) -> None:
    while True:
        clear()
        print_logo()
        print_status_bar(state)
        section("WAN & Router")

        r    = state.get("router", {})
        mgmt = r.get("mgmt_enabled", False)
        table(["Setting", "Value"], [
            ["Hostname",       r.get("hostname", "")],
            ["WAN Interface",  r.get("wan_interface", "")],
            ["WAN Mode",       r.get("wan_mode", "")],
            ["WAN IP",         r.get("wan_ip", "—") if r.get("wan_mode") == "static" else "(DHCP)"],
            ["Upstream DNS",   r.get("wan_dns", "")],
            ["Mgmt Interface", (ok("on") + f" {r.get('mgmt_interface','')} {r.get('mgmt_ip','')}/{r.get('mgmt_prefix','')}") if mgmt else dim("off")],
        ])

        idx = menu("WAN Actions", [
            ("Edit WAN settings",           ""),
            ("Toggle management interface", ok("on") if mgmt else dim("off")),
            ("Reload",                      ""),
        ])
        if idx == -1:
            return
        if idx == 0:
            _edit_wan(state)
        elif idx == 1:
            _toggle_mgmt(state)
        state = GET("/api/state")


def _edit_wan(state: dict) -> None:
    section("Edit WAN Settings")
    r = state.get("router", {})
    try:
        ifaces = [i["name"] for i in GET("/api/interfaces")]
        print(dim(f"  Interfaces: {', '.join(ifaces)}"))
        hostname = prompt("Hostname",      r.get("hostname", "spud-router"))
        wan_if   = prompt("WAN interface", r.get("wan_interface", "eth1"))
        wan_mode = prompt("WAN mode [dhcp/static]", r.get("wan_mode", "dhcp"))
        wan_ip = wan_prefix = wan_gw = None
        if wan_mode == "static":
            wan_ip     = prompt("WAN IP",   r.get("wan_ip", ""))
            wan_prefix = int(prompt("Prefix", str(r.get("wan_prefix", 24))))
            wan_gw     = prompt("Gateway",  r.get("wan_gateway", ""))
        wan_dns = prompt("Upstream DNS", r.get("wan_dns", "1.1.1.1"))
    except (ValueError, KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        POST("/api/router", {
            **r,
            "hostname":     hostname,
            "wan_interface": wan_if,
            "wan_mode":     wan_mode,
            "wan_ip":       wan_ip,
            "wan_prefix":   wan_prefix,
            "wan_gateway":  wan_gw,
            "wan_dns":      wan_dns,
        })
        print(ok("\n  ✓ WAN settings saved"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _toggle_mgmt(state: dict) -> None:
    r       = state.get("router", {})
    enabled = r.get("mgmt_enabled", False)
    action  = "Disable" if enabled else "Enable"
    if not confirm(f"{action} management interface?"):
        return
    try:
        POST("/api/router", {**r, "mgmt_enabled": not enabled})
        print(ok(f"\n  ✓ Management interface {'disabled' if enabled else 'enabled'}"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()
