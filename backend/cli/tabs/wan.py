# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
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

        r        = state.get("router", {})
        mgmt     = r.get("mgmt_enabled", False)
        dns_mode = r.get("wan_dns_mode", "auto")
        dns_value = "(from WAN DHCP)"
        if dns_mode == "manual":
            dns_value = ", ".join(s for s in (r.get("wan_dns"), r.get("wan_dns_alt")) if s)
        elif dns_mode == "doh":
            provider = r.get("doh_provider", "cloudflare")
            dns_value = r.get("doh_custom_url", "") if provider == "custom" else provider
        table(["Setting", "Value"], [
            ["Hostname",       r.get("hostname", "")],
            ["WAN Interface",  r.get("wan_interface", "")],
            ["WAN Mode",       r.get("wan_mode", "")],
            ["WAN IP",         r.get("wan_ip", "—") if r.get("wan_mode") == "static" else "(DHCP)"],
            ["DNS Source",     dns_mode],
            ["Upstream DNS",   dns_value],
            ["Block WAN :53",  (warn("on") if dns_mode == "doh" else dim("on (inactive — not in DoH mode)")) if r.get("block_wan_dns") else dim("off")],
            ["Mgmt Interface", _mgmt_summary(r) if mgmt else dim("off")],
        ])

        idx = menu("WAN Actions", [
            ("Edit WAN settings",           ""),
            ("Toggle management interface", ok("on") if mgmt else dim("off")),
            ("Edit management addressing",  r.get("mgmt_addr_mode", "static") if mgmt else dim("—")),
            ("Toggle mgmt ping (ICMP echo)", ok("on") if r.get("mgmt_icmp_echo") else dim("off")),
            ("Toggle mgmt web UI (port 8080)", ok("on") if r.get("mgmt_web_ui", True) else dim("off")),
            ("Toggle block LAN plaintext DNS to WAN", ok("on") if r.get("block_wan_dns") else dim("off")),
            ("Reload",                      ""),
        ])
        if idx == -1:
            return
        if idx == 0:
            _edit_wan(state)
        elif idx == 1:
            _toggle_mgmt(state)
        elif idx == 2:
            _edit_mgmt(state)
        elif idx == 3:
            _toggle_mgmt_icmp_echo(state)
        elif idx == 4:
            _toggle_mgmt_web_ui(state)
        elif idx == 5:
            _toggle_block_wan_dns(state)
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
        dns_mode = prompt("DNS source [auto/manual/doh]", r.get("wan_dns_mode", "auto"))
        wan_dns = r.get("wan_dns", "1.1.1.1")
        wan_dns_alt = r.get("wan_dns_alt", "")
        doh_provider = r.get("doh_provider", "cloudflare")
        doh_custom_url = r.get("doh_custom_url") or ""
        if dns_mode == "manual":
            wan_dns     = prompt("Upstream DNS",           r.get("wan_dns", "1.1.1.1"))
            wan_dns_alt = prompt("Upstream DNS (secondary, blank for none)", r.get("wan_dns_alt", "") or "")
        elif dns_mode == "doh":
            doh_provider = prompt("DoH provider [cloudflare/quad9/google/custom]", doh_provider)
            if doh_provider == "custom":
                doh_custom_url = prompt("Custom DoH URL (https://...)", doh_custom_url)
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
            "doh_provider": doh_provider,
            "doh_custom_url": doh_custom_url or None,
        })
        print(ok("\n  ✓ WAN settings saved"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _mgmt_summary(r: dict) -> str:
    """One-line management-interface summary showing the addressing mode (#213),
    so the TUI matches what the Web UI's WAN tab shows."""
    iface = r.get("mgmt_interface", "")
    if r.get("mgmt_addr_mode", "static") == "dhcp":
        return ok("on") + f" {iface} (DHCP client)"
    srv = " +DHCP" if r.get("mgmt_dhcp_server", True) else ""
    return ok("on") + f" {iface} {r.get('mgmt_ip','')}/{r.get('mgmt_prefix','')} static{srv}"


def _edit_mgmt(state: dict) -> None:
    """Edit the management interface's addressing — DHCP client or static (#213),
    matching the Web UI's WAN tab. In DHCP mode spud-router takes a lease from an
    existing management network and serves no DHCP of its own (the backend
    rejects being both client and server on the same interface)."""
    section("Management Interface Addressing")
    r = state.get("router", {})
    if not r.get("mgmt_enabled"):
        print(warn("  Management interface is disabled — enable it first."))
        pause()
        return
    try:
        mode = prompt("Addressing mode [dhcp/static]", r.get("mgmt_addr_mode", "static"))
        if mode not in ("dhcp", "static"):
            print(err("  Mode must be 'dhcp' or 'static'."))
            pause()
            return
        payload = {**r, "mgmt_addr_mode": mode}
        if mode == "dhcp":
            print(dim("\n  DHCP client — takes a lease from your management network's own DHCP"))
            print(dim("  server (pin it with a reservation). spud-router serves no DHCP here,"))
            print(dim("  and the lease's default route/DNS are suppressed so WAN stays default."))
            payload["mgmt_dhcp_server"] = False   # backend rejects client + server on one iface
        else:
            payload["mgmt_ip"]     = prompt("Mgmt IP",  r.get("mgmt_ip", "192.168.1.1"))
            payload["mgmt_prefix"] = int(prompt("Prefix", str(r.get("mgmt_prefix", 24))))
            serve = confirm("Serve DHCP on the management interface?")
            payload["mgmt_dhcp_server"] = serve
            if serve:
                payload["mgmt_dhcp_start"] = prompt("DHCP range start", r.get("mgmt_dhcp_start", "192.168.1.100"))
                payload["mgmt_dhcp_end"]   = prompt("DHCP range end",   r.get("mgmt_dhcp_end", "192.168.1.150"))
    except (ValueError, KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return
    try:
        POST("/api/router", payload)
        print(ok(f"\n  ✓ Management addressing set to {mode}"))
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


def _toggle_mgmt_web_ui(state: dict) -> None:
    r       = state.get("router", {})
    enabled = r.get("mgmt_web_ui", True)
    action  = "Disable" if enabled else "Enable"
    if enabled:
        print(warn("\n  ⚠ Disabling the web UI on the management interface could lock you out"))
        print(warn("  of it here. The web UI must stay reachable on at least one network —"))
        print(warn("  saving is refused if this would be the last one."))
    if not confirm(f"{action} web UI (port 8080) on the management interface?"):
        return
    try:
        POST("/api/router", {**r, "mgmt_web_ui": not enabled})
        print(ok(f"\n  ✓ Mgmt web UI {'disabled' if enabled else 'enabled'}"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _toggle_block_wan_dns(state: dict) -> None:
    r       = state.get("router", {})
    enabled = r.get("block_wan_dns", False)
    target  = not enabled

    section("Block LAN Plaintext DNS to WAN")
    if r.get("wan_dns_mode") != "doh":
        print(warn("  ⚠ DNS Source is not set to DoH — this toggle has no effect until it is."))
    if target:
        print(warn("\n  ⚠ This blocks LAN clients from reaching WAN port 53 directly. Devices with"))
        print(warn("  hardcoded DNS servers (not using this router's DHCP-assigned DNS) will lose"))
        print(warn("  DNS resolution. Independent of DoH — DoH itself works without this toggle."))
    if not confirm(f"{'Enable' if target else 'Disable'} the block?"):
        return
    try:
        POST("/api/router", {**r, "block_wan_dns": target})
        print(ok(f"\n  ✓ Block LAN plaintext DNS to WAN {'enabled' if target else 'disabled'}"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()
