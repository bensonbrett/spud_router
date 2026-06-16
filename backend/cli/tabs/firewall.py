"""Firewall rules tab — inbound and inter-VLAN."""
from ..api import DELETE, GET, POST
from ..ui import (
    bold, dim, err, ok, warn,
    clear, menu, pause, print_logo,
    print_status_bar, prompt, section, table,
)


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
                pp    = r.get("proto", "any") + (f":{r['port']}" if r.get("port") else "")
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
                pp = r.get("proto", "any") + (f":{r['port']}" if r.get("port") else "")
                rows.append([
                    ok("ALLOW") if r.get("action") == "accept" else err("DROP"),
                    f"{fv} → {tv}", pp, r.get("description", ""),
                ])
            table(["Action", "Flow", "Proto:Port", "Description"], rows)

        idx = menu("Firewall Actions", [
            ("Add inbound rule",       "Traffic reaching this router"),
            ("Remove inbound rule",    ""),
            ("Add inter-VLAN rule",    "Traffic between VLANs"),
            ("Remove inter-VLAN rule", ""),
            ("Reload",                 ""),
        ])
        if idx == -1:
            return
        if idx == 0: _add_inbound(state)
        elif idx == 1: _del_inbound(state)
        elif idx == 2: _add_intervlan(state)
        elif idx == 3: _del_intervlan(state)
        state = GET("/api/state")


def _add_inbound(state: dict) -> None:
    section("Add Inbound Rule")
    vlans = state.get("vlans", [])
    print(dim("  0 = all VLANs  |  DNS (53) + DHCP (67) always open"))
    if vlans:
        print(dim("  " + "  ".join(f"{v['vlan_id']}={v['name']}" for v in vlans)))
    try:
        vlan_id  = int(prompt("VLAN ID (0 for all)", "0"))
        proto    = prompt("Protocol [tcp/udp/any]", "tcp")
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
        proto     = prompt("Protocol [tcp/udp/any]", "any")
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
