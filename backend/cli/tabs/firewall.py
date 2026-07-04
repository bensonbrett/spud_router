# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Firewall rules tab — inbound, inter-VLAN, outbound (egress), and port forwarding (DNAT)."""
from ..api import DELETE, GET, POST, PUT
from ..ui import (
    bold, dim, err, ok, warn,
    clear, confirm, menu, pause, print_logo,
    print_status_bar, prompt, section, table,
)


def _proto_summary(r: dict) -> str:
    proto = r.get("proto", "any")
    if proto == "icmp":
        t = r.get("icmp_type")
        c = r.get("icmp_code")
        if not t:
            return "icmp"
        return f"icmp/{t}" + (f":{c}" if c is not None else "")
    return proto + (f":{r['port']}" if r.get("port") else "")


def screen(state: dict) -> None:
    while True:
        clear()
        print_logo()
        print_status_bar(state)
        section("Firewall")

        fw_in   = state.get("fw_inbound", [])
        fw_iv   = state.get("fw_intervlan", [])
        vlan_map = {v["vlan_id"]: v["name"] for v in state.get("vlans", [])}

        # Inbound rules summary
        print(f"  {bold(f'Inbound Rules ({len(fw_in)})')}  {dim('DNS/DHCP always open')}")
        if fw_in:
            rows = []
            for r in fw_in:
                vid   = r.get("vlan_id", 0)
                vname = vlan_map.get(vid, "All VLANs") if vid != 0 else "All VLANs"
                pp    = _proto_summary(r)
                rows.append([
                    ok("ALLOW") if r.get("action") == "accept" else err("DROP"),
                    vname, pp, r.get("description", ""),
                ])
            table(["Action", "VLAN", "Proto:Port", "Description"], rows)
        else:
            print(dim("  No inbound rules."))

        # Inter-VLAN rules summary
        print()
        has_iv = len(fw_iv) > 0
        mode   = warn("explicit — default deny") if has_iv else ok("auto — non-isolated VLANs meshed")
        print(f"  {bold(f'Inter-VLAN Rules ({len(fw_iv)})')}  {dim('mode:')} {mode}")
        if fw_iv:
            rows = []
            for r in fw_iv:
                fv = vlan_map.get(r.get("from_vlan", 0), "All") if r.get("from_vlan", 0) != 0 else "All"
                tv = vlan_map.get(r.get("to_vlan",   0), "All") if r.get("to_vlan",   0) != 0 else "All"
                pp = _proto_summary(r)
                rows.append([
                    ok("ALLOW") if r.get("action") == "accept" else err("DROP"),
                    f"{fv} → {tv}", pp, r.get("description", ""),
                ])
            table(["Action", "Flow", "Proto:Port", "Description"], rows)

        # Outbound (egress) rules summary
        print()
        fw_out  = state.get("fw_outbound", [])
        out_default = state.get("fw_outbound_default", "allow")
        default_str = ok("allow") if out_default == "allow" else err("deny")
        print(f"  {bold(f'Outbound Rules ({len(fw_out)})')}  {dim('default:')} {default_str}")
        if fw_out:
            rows = []
            for r in fw_out:
                vid   = r.get("vlan_id", 0)
                vname = vlan_map.get(vid, "All VLANs") if vid != 0 else "All VLANs"
                pp    = _proto_summary(r)
                rows.append([
                    ok("ALLOW") if r.get("action") == "accept" else err("DROP"),
                    vname, r.get("dest") or "any", pp, r.get("description", ""),
                ])
            table(["Action", "VLAN", "Dest", "Proto:Port", "Description"], rows)
        else:
            print(dim("  No outbound rules — first-match falls through to the default above."))

        # Port forwarding (DNAT) summary
        print()
        pfs = state.get("port_forwards", [])
        print(f"  {bold(f'Port Forwards ({len(pfs)})')}  {dim('WAN port → LAN host:port')}")
        if pfs:
            rows = []
            for pf in pfs:
                rows.append([
                    ok("enabled") if pf.get("enabled", True) else dim("disabled"),
                    pf.get("proto", "tcp"),
                    f"WAN:{pf.get('wan_port')} → {pf.get('lan_host')}:{pf.get('lan_port')}",
                    pf.get("description", ""),
                ])
            table(["Status", "Proto", "Forward", "Description"], rows)
        else:
            print(dim("  No port forwards."))

        idx = menu("Firewall Actions", [
            ("Add inbound rule",       "Traffic reaching this router"),
            ("Remove inbound rule",    ""),
            ("Add inter-VLAN rule",    "Traffic between VLANs"),
            ("Remove inter-VLAN rule", ""),
            ("Add outbound rule",      "LAN VLANs → WAN"),
            ("Remove outbound rule",   ""),
            ("Toggle default outbound policy", "Allow ↔ Deny"),
            ("Add port forward",       "WAN port → LAN host:port (DNAT)"),
            ("Remove port forward",    ""),
            ("Toggle port forward enabled", ""),
            ("Reload",                 ""),
        ])
        if idx == -1:
            return
        if idx == 0: _add_inbound(state)
        elif idx == 1: _del_inbound(state)
        elif idx == 2: _add_intervlan(state)
        elif idx == 3: _del_intervlan(state)
        elif idx == 4: _add_outbound(state)
        elif idx == 5: _del_outbound(state)
        elif idx == 6: _toggle_outbound_default(state)
        elif idx == 7: _add_port_forward(state)
        elif idx == 8: _del_port_forward(state)
        elif idx == 9: _toggle_port_forward(state)
        state = GET("/api/state")


def _prompt_icmp_type_code() -> tuple[str | None, int | None]:
    """Prompt for optional ICMP type/code when proto=icmp. Whitelisted names
    or a numeric 0-255 type; the model re-validates regardless."""
    print(dim("  ICMP types: echo-request, echo-reply, destination-unreachable, time-exceeded, any, or a number 0-255"))
    icmp_type_str = prompt("ICMP type (Enter for any)")
    icmp_type = icmp_type_str if icmp_type_str else None
    icmp_code_str = prompt("ICMP code (Enter for none)")
    icmp_code = int(icmp_code_str) if icmp_code_str else None
    return icmp_type, icmp_code


def _add_inbound(state: dict) -> None:
    section("Add Inbound Rule")
    vlans = state.get("vlans", [])
    print(dim("  0 = all VLANs  |  DNS (53) + DHCP (67) always open"))
    if vlans:
        print(dim("  " + "  ".join(f"{v['vlan_id']}={v['name']}" for v in vlans)))
    try:
        vlan_id  = int(prompt("VLAN ID (0 for all)", "0"))
        proto    = prompt("Protocol [tcp/udp/icmp/any]", "tcp")
        port = icmp_type = icmp_code = None
        if proto == "icmp":
            icmp_type, icmp_code = _prompt_icmp_type_code()
        else:
            port_str = prompt("Port (Enter for any)")
            port     = int(port_str) if port_str else None
        action   = prompt("Action [accept/drop]", "accept")
        desc     = prompt("Description")
    except (ValueError, KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        POST("/api/firewall/inbound", {
            "vlan_id": vlan_id, "proto": proto,
            "port": port, "action": action, "description": desc,
            "icmp_type": icmp_type, "icmp_code": icmp_code,
        })
        print(ok("\n  ✓ Inbound rule added"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _del_inbound(state: dict) -> None:
    rules    = state.get("fw_inbound", [])
    vlan_map = {v["vlan_id"]: v["name"] for v in state.get("vlans", [])}
    if not rules:
        print(dim("  No inbound rules."))
        pause()
        return

    idx = menu(
        "Remove inbound rule",
        [(r.get("description") or f"{r.get('proto')}:{r.get('port','*')}",
          f"VLAN {vlan_map.get(r['vlan_id'],'All')} {r['action']}") for r in rules],
        "Cancel",
    )
    if idx == -1:
        return

    try:
        DELETE(f"/api/firewall/inbound/{rules[idx]['id']}")
        print(ok("\n  ✓ Rule removed"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _add_intervlan(state: dict) -> None:
    section("Add Inter-VLAN Rule")
    vlans = state.get("vlans", [])
    if vlans:
        print(dim("  0=All  " + "  ".join(f"{v['vlan_id']}={v['name']}" for v in vlans)))
    print(dim("  First rule switches to explicit mode (default deny between VLANs)"))
    try:
        from_vlan = int(prompt("From VLAN (0 for all)", "0"))
        to_vlan   = int(prompt("To VLAN   (0 for all)", "0"))
        proto     = prompt("Protocol [tcp/udp/icmp/any]", "any")
        port = icmp_type = icmp_code = None
        if proto == "icmp":
            icmp_type, icmp_code = _prompt_icmp_type_code()
        else:
            port_str  = prompt("Port (Enter for any)")
            port      = int(port_str) if port_str else None
        action    = prompt("Action [accept/drop]", "accept")
        desc      = prompt("Description")
    except (ValueError, KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        POST("/api/firewall/intervlan", {
            "from_vlan": from_vlan, "to_vlan": to_vlan,
            "proto": proto, "port": port,
            "action": action, "description": desc,
            "icmp_type": icmp_type, "icmp_code": icmp_code,
        })
        print(ok("\n  ✓ Inter-VLAN rule added"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _del_intervlan(state: dict) -> None:
    rules    = state.get("fw_intervlan", [])
    vlan_map = {v["vlan_id"]: v["name"] for v in state.get("vlans", [])}
    if not rules:
        print(dim("  No inter-VLAN rules."))
        pause()
        return

    idx = menu(
        "Remove inter-VLAN rule",
        [(r.get("description") or f"VLAN {r['from_vlan']} → {r['to_vlan']}",
          f"{r.get('proto','any')} {r['action']}") for r in rules],
        "Cancel",
    )
    if idx == -1:
        return

    try:
        DELETE(f"/api/firewall/intervlan/{rules[idx]['id']}")
        print(ok("\n  ✓ Rule removed"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _add_outbound(state: dict) -> None:
    section("Add Outbound Rule")
    vlans = state.get("vlans", [])
    print(dim("  0 = all LAN VLANs (source)  |  destination blank = any"))
    if vlans:
        print(dim("  " + "  ".join(f"{v['vlan_id']}={v['name']}" for v in vlans)))
    try:
        vlan_id  = int(prompt("Source VLAN ID (0 for all)", "0"))
        dest     = prompt("Destination (CIDR/IP, Enter for any)")
        proto    = prompt("Protocol [tcp/udp/icmp/any]", "any")
        port = icmp_type = icmp_code = None
        if proto == "icmp":
            icmp_type, icmp_code = _prompt_icmp_type_code()
        else:
            port_str = prompt("Port (Enter for any)")
            port     = int(port_str) if port_str else None
        action   = prompt("Action [accept/drop]", "accept")
        desc     = prompt("Description")
    except (ValueError, KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        POST("/api/firewall/outbound", {
            "vlan_id": vlan_id, "dest": dest, "proto": proto,
            "port": port, "action": action, "description": desc,
            "icmp_type": icmp_type, "icmp_code": icmp_code,
        })
        print(ok("\n  ✓ Outbound rule added"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _del_outbound(state: dict) -> None:
    rules    = state.get("fw_outbound", [])
    vlan_map = {v["vlan_id"]: v["name"] for v in state.get("vlans", [])}
    if not rules:
        print(dim("  No outbound rules."))
        pause()
        return

    idx = menu(
        "Remove outbound rule",
        [(r.get("description") or f"{r.get('proto')}:{r.get('port','*')}",
          f"VLAN {vlan_map.get(r['vlan_id'],'All')} → {r.get('dest') or 'any'} {r['action']}") for r in rules],
        "Cancel",
    )
    if idx == -1:
        return

    try:
        DELETE(f"/api/firewall/outbound/{rules[idx]['id']}")
        print(ok("\n  ✓ Rule removed"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _toggle_outbound_default(state: dict) -> None:
    current = state.get("fw_outbound_default", "allow")
    target  = "deny" if current == "allow" else "allow"

    section("Default Outbound Policy")
    print(f"  Current default: {ok('allow') if current == 'allow' else err('deny')}")
    if target == "deny":
        print(warn("\n  ⚠ Switching to Deny blocks all LAN internet access except what your"))
        print(warn("  rules explicitly allow. The router's own connectivity and the web UI"))
        print(warn("  are unaffected, but LAN devices will lose internet until you add"))
        print(warn("  allow rules."))
    if not confirm(f"Switch default to '{target}'?"):
        return

    try:
        PUT("/api/firewall/outbound/default", {"default": target})
        print(ok(f"\n  ✓ Default outbound policy set to '{target}'"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _add_port_forward(state: dict) -> None:
    section("Add Port Forward")
    print(dim("  Forwards a WAN port to a host:port on the LAN (DNAT)."))
    try:
        proto     = prompt("Protocol [tcp/udp]", "tcp")
        wan_port  = int(prompt("WAN port"))
        lan_host  = prompt("LAN host IP")
        lan_port  = int(prompt("LAN port", str(wan_port)))
        desc      = prompt("Description")
    except (ValueError, KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        POST("/api/firewall/port-forward", {
            "proto": proto, "wan_port": wan_port,
            "lan_host": lan_host, "lan_port": lan_port,
            "description": desc, "enabled": True,
        })
        print(ok("\n  ✓ Port forward added"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _del_port_forward(state: dict) -> None:
    pfs = state.get("port_forwards", [])
    if not pfs:
        print(dim("  No port forwards."))
        pause()
        return

    idx = menu(
        "Remove port forward",
        [(pf.get("description") or f"{pf.get('proto')} WAN:{pf.get('wan_port')}",
          f"→ {pf.get('lan_host')}:{pf.get('lan_port')}") for pf in pfs],
        "Cancel",
    )
    if idx == -1:
        return

    try:
        DELETE(f"/api/firewall/port-forward/{pfs[idx]['id']}")
        print(ok("\n  ✓ Port forward removed"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _toggle_port_forward(state: dict) -> None:
    pfs = state.get("port_forwards", [])
    if not pfs:
        print(dim("  No port forwards."))
        pause()
        return

    idx = menu(
        "Toggle port forward",
        [(pf.get("description") or f"{pf.get('proto')} WAN:{pf.get('wan_port')}",
          "enabled" if pf.get("enabled", True) else "disabled") for pf in pfs],
        "Cancel",
    )
    if idx == -1:
        return

    pf = dict(pfs[idx])
    pf["enabled"] = not pf.get("enabled", True)
    try:
        PUT(f"/api/firewall/port-forward/{pf['id']}", pf)
        print(ok(f"\n  ✓ Port forward {'enabled' if pf['enabled'] else 'disabled'}"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()
