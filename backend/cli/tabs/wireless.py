"""Wireless AP configuration tab."""
from ..api import DELETE, GET, POST
from ..ui import (
    bold, dim, err, hi, ok, warn,
    clear, confirm, menu, pause, print_logo,
    print_status_bar, prompt, section, table,
)

SECURITY_LABELS = {
    "wpa2":   "WPA2-PSK",
    "wpa3":   "WPA3-SAE",
    "wpa2/3": "WPA2/WPA3 mixed",
    "open":   "Open (no encryption)",
}


def screen(state: dict) -> None:
    while True:
        clear()
        print_logo()
        print_status_bar(state)
        section("Wireless AP")

        try:
            wireless = GET("/api/wireless")
        except RuntimeError as e:
            print(err(f"  Error: {e}"))
            pause()
            return

        enabled  = wireless.get("enabled", False)
        iface    = wireless.get("interface", "wlan0")
        country  = wireless.get("country_code", "US")
        ssids    = wireless.get("ssids", [])
        vlan_map = {v["vlan_id"]: v["name"] for v in state.get("vlans", [])}

        # Status summary
        print(f"  {'Enabled:':<16} {ok('yes') if enabled else dim('no')}")
        print(f"  {'Interface:':<16} {hi(iface)}")
        print(f"  {'Country:':<16} {hi(country)}")

        # Hardware capability check
        try:
            ifaces = GET("/api/wireless/interfaces")
            active = next((i for i in ifaces if i["name"] == iface), None)
            if active:
                ap_str = ok("AP supported") if active.get("supports_ap") else warn("AP NOT supported")
                vaps   = active.get("max_vaps", 0)
                print(f"  {'Hardware:':<16} {ap_str}  max {vaps} SSID{'s' if vaps != 1 else ''}")
                if active.get("driver"):
                    print(f"  {'Driver:':<16} {dim(active['driver'])}")
                if not active.get("supports_ap"):
                    print()
                    print(warn("  ⚠ This interface does not support AP mode."))
                    print(dim("  Check: iw list | grep -A5 'interface modes'"))
        except RuntimeError:
            pass

        # SSID list
        if ssids:
            print()
            rows = []
            for s in ssids:
                vname = vlan_map.get(s["vlan_id"], "?")
                rows.append([
                    s["ssid"],
                    f"VLAN {s['vlan_id']} ({vname})",
                    f"{s['band']} GHz",
                    SECURITY_LABELS.get(s["security"], s["security"]),
                    ok("on") if s.get("enabled", True) else dim("off"),
                    dim("hidden") if s.get("hidden") else "",
                ])
            table(["SSID", "VLAN", "Band", "Security", "State", ""], rows)
        else:
            print(dim("\n  No SSIDs configured."))

        idx = menu("Wireless Actions", [
            ("Toggle enable/disable", ""),
            ("Edit global settings",  "Interface, country code"),
            ("Add SSID",              ""),
            ("Edit SSID",             ""),
            ("Remove SSID",           ""),
            ("Reload",                ""),
        ])
        if idx == -1:
            return
        if idx == 0:   _toggle(wireless)
        elif idx == 1: _edit_global(wireless)
        elif idx == 2: _add_ssid(state)
        elif idx == 3: _edit_ssid(state, wireless)
        elif idx == 4: _delete_ssid(wireless)
        state = GET("/api/state")


def _toggle(wireless: dict) -> None:
    enabled = wireless.get("enabled", False)
    action  = "Disable" if enabled else "Enable"
    if not confirm(f"{action} wireless AP?"):
        return
    try:
        POST("/api/wireless", {**wireless, "enabled": not enabled})
        print(ok(f"\n  ✓ Wireless {'disabled' if enabled else 'enabled'}"))
        print(dim("  Run Apply to activate."))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _edit_global(wireless: dict) -> None:
    section("Edit Wireless Settings")
    try:
        ifaces = GET("/api/wireless/interfaces")
        if ifaces:
            print(dim("  Detected: " + ", ".join(
                i["name"] + (" ✓" if i.get("supports_ap") else " ✗ no AP") for i in ifaces
            )))
    except RuntimeError:
        pass

    try:
        iface   = prompt("Interface", wireless.get("interface", "wlan0"))
        country = prompt("Country code (2 letters, e.g. US, GB)", wireless.get("country_code", "US"))
        country = country.upper()[:2]
    except (KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        POST("/api/wireless", {**wireless, "interface": iface, "country_code": country})
        print(ok("\n  ✓ Settings saved"))
        print(dim("  Run Apply to activate."))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _add_ssid(state: dict) -> None:
    section("Add SSID")
    vlans   = state.get("vlans", [])

    if not vlans:
        print(warn("  No VLANs configured — create a VLAN first."))
        pause()
        return

    vlan_hint = "  VLANs: " + "  ".join(f"{v['vlan_id']}={v['name']}" for v in vlans)
    print(dim(vlan_hint))

    try:
        ssid_name = prompt("SSID name (network name, max 32 chars)")
        vlan_id   = int(prompt("VLAN ID"))
        band      = prompt("Band [2.4/5]", "2.4")
        channel   = prompt("Channel [auto/1-14 for 2.4GHz, 36-165 for 5GHz]", "auto")
        security  = prompt("Security [wpa2/wpa3/wpa2/3/open]", "wpa2")
        password  = ""
        if security != "open":
            import getpass
            while True:
                password = getpass.getpass("  › Password (min 8 chars): ")
                if len(password) >= 8:
                    break
                print(err("  Password must be at least 8 characters."))
        hidden  = confirm("Hide SSID (don't broadcast)?")
        enabled = True
    except (ValueError, KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        POST("/api/wireless/ssids", {
            "ssid":     ssid_name,
            "vlan_id":  vlan_id,
            "band":     band,
            "channel":  channel,
            "security": security,
            "password": password,
            "hidden":   hidden,
            "enabled":  enabled,
        })
        print(ok(f"\n  ✓ SSID '{ssid_name}' added"))
        print(dim("  Run Apply to activate."))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _edit_ssid(state: dict, wireless: dict) -> None:
    ssids = wireless.get("ssids", [])
    if not ssids:
        print(dim("  No SSIDs to edit."))
        pause()
        return

    idx = menu(
        "Select SSID to edit",
        [(s["ssid"], f"VLAN {s['vlan_id']} · {s['band']}GHz · {s['security']}") for s in ssids],
        "Cancel",
    )
    if idx == -1:
        return

    s = ssids[idx]
    section(f"Edit SSID: {s['ssid']}")

    vlans     = state.get("vlans", [])
    vlan_hint = "  VLANs: " + "  ".join(f"{v['vlan_id']}={v['name']}" for v in vlans)
    print(dim(vlan_hint))

    try:
        ssid_name = prompt("SSID name", s["ssid"])
        vlan_id   = int(prompt("VLAN ID", str(s["vlan_id"])))
        band      = prompt("Band [2.4/5]", s.get("band", "2.4"))
        channel   = prompt("Channel", s.get("channel", "auto"))
        security  = prompt("Security [wpa2/wpa3/wpa2/3/open]", s.get("security", "wpa2"))
        password  = s.get("password", "")
        if security != "open":
            import getpass
            change = confirm(f"Change password? (current set)")
            if change:
                while True:
                    password = getpass.getpass("  › New password (min 8 chars): ")
                    if len(password) >= 8:
                        break
                    print(err("  Password must be at least 8 characters."))
        hidden  = confirm(f"Hide SSID?") if not s.get("hidden") else confirm("Currently hidden — show SSID?") == False
        enabled = confirm("Enable this SSID?")
    except (ValueError, KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        POST(f"/api/wireless/ssids/{s['id']}", {
            "id":       s["id"],
            "ssid":     ssid_name,
            "vlan_id":  vlan_id,
            "band":     band,
            "channel":  channel,
            "security": security,
            "password": password,
            "hidden":   hidden,
            "enabled":  enabled,
        })
        print(ok(f"\n  ✓ SSID '{ssid_name}' updated"))
        print(dim("  Run Apply to activate."))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _delete_ssid(wireless: dict) -> None:
    ssids = wireless.get("ssids", [])
    if not ssids:
        print(dim("  No SSIDs to remove."))
        pause()
        return

    idx = menu(
        "Remove SSID",
        [(s["ssid"], f"VLAN {s['vlan_id']} · {s['security']}") for s in ssids],
        "Cancel",
    )
    if idx == -1:
        return

    s = ssids[idx]
    if not confirm(f"Remove SSID '{s['ssid']}'?"):
        return

    try:
        DELETE(f"/api/wireless/ssids/{s['id']}")
        print(ok(f"\n  ✓ '{s['ssid']}' removed"))
        print(dim("  Run Apply to activate."))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()
