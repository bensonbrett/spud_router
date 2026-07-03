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
            ["DNS Source",     r.get("wan_dns_mode", "auto")],
            ["Upstream DNS",   ", ".join(s for s in (r.get("wan_dns"), r.get("wan_dns_alt")) if s) if r.get("wan_dns_mode", "auto") == "manual" else "(from WAN DHCP)"],
            ["Mgmt Interface", (ok("on") + f" {r.get('mgmt_interface','')} {r.get('mgmt_ip','')}/{r.get('mgmt_prefix','')}") if mgmt else dim("off")],
        ])

        idx = menu("WAN Actions", [
            ("Edit WAN settings",           ""),
            ("Toggle management interface", ok("on") if mgmt else dim("off")),
            ("Toggle mgmt ping (ICMP echo)", ok("on") if r.get("mgmt_icmp_echo") else dim("off")),
            ("Reload",                      ""),
        ])
        if idx == -1:
            return
        if idx == 0:
            _edit_wan(state)
        elif idx == 1:
            _toggle_mgmt(state)
        elif idx == 2:
            _toggle_mgmt_icmp_echo(state)
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
        dns_mode = prompt("DNS source [auto/manual]", r.get("wan_dns_mode", "auto"))
        wan_dns = r.get("wan_dns", "1.1.1.1")
        wan_dns_alt = r.get("wan_dns_alt", "")
        if dns_mode == "manual":
            wan_dns     = prompt("Upstream DNS",           r.get("wan_dns", "1.1.1.1"))
            wan_dns_alt = prompt("Upstream DNS (secondary, blank for none)", r.get("wan_dns_alt", "") or "")
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
            "wan_dns_mode": dns_mode,
            "wan_dns":      wan_dns,
            "wan_dns_alt":  wan_dns_alt or None,
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


def _toggle_mgmt_icmp_echo(state: dict) -> None:
    r       = state.get("router", {})
    enabled = r.get("mgmt_icmp_echo", False)
    action  = "Block" if enabled else "Allow"
    if not confirm(f"{action} ping (ICMP echo) on the management interface?"):
        return
    try:
        POST("/api/router", {**r, "mgmt_icmp_echo": not enabled})
        print(ok(f"\n  ✓ Mgmt ping {'blocked' if enabled else 'allowed'}"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()
