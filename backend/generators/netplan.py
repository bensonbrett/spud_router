# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Netplan configuration generator.

Produces a netplan YAML file that configures:
  - WAN interface (DHCP or static)
  - Trunk parent interfaces (no IP — carriers for VLAN subinterfaces)
  - Management interface IP (for untagged direct access)
  - 802.1Q VLAN subinterfaces with gateway IPs
  - Per-VLAN static routes
  - Wireless AP bridges (when wireless is enabled)
"""
from . import hostapd as hostapd_gen


def generate(state: dict) -> str:
    """
    Generate the contents of /etc/netplan/50-spud-router.yaml.

    Args:
        state: Full router state dict from state.load_state()

    Returns:
        YAML string ready to write to disk.
    """
    router = state.get("router", {})
    vlans  = state.get("vlans", [])
    routes = state.get("static_routes", [])

    wan          = router.get("wan_interface", "eth1")
    wan_mode     = router.get("wan_mode", "dhcp")
    wan_is_vlan  = "." in wan
    mgmt_enabled = router.get("mgmt_enabled", False)
    mgmt_if      = router.get("mgmt_interface", "eth0")
    mgmt_ip      = router.get("mgmt_ip", "192.168.1.1")
    mgmt_prefix  = router.get("mgmt_prefix", 24)

    # vlan_id == 0 is the "untagged / physical interface" sentinel (#195,
    # multi-NIC installs): the network lives directly on its own port with
    # no 802.1Q tag, so it belongs under ethernets: like WAN's physical
    # case, not under vlans:.
    tagged_vlans   = [v for v in vlans if v.get("vlan_id") != 0]
    untagged_vlans = [v for v in vlans if v.get("vlan_id") == 0 and v.get("ip_address")]
    # A physical NIC can carry BOTH an untagged network and a tagged VLAN
    # subinterface at once (#207's 2-NIC "management VLAN" composition: LAN
    # stays untagged directly on the NIC while management rides a tagged
    # subinterface of that same NIC). Index by interface so the trunk-parent
    # loop below can tell "bare carrier, no address" apart from "carrier that
    # is ALSO itself an addressed untagged network".
    untagged_by_if = {v["interface"]: v for v in untagged_vlans}

    lines = ["network:", "  version: 2", "  renderer: networkd", "", "  ethernets:"]
    emitted_ethernets: set[str] = set()

    # WAN — physical interface (not a VLAN subinterface)
    if not wan_is_vlan:
        if wan_mode == "dhcp":
            lines += [f"    {wan}:", "      dhcp4: true"]
        elif wan_mode == "static":
            lines += [
                f"    {wan}:",
                f"      addresses: [{router['wan_ip']}/{router['wan_prefix']}]",
                "      routes:",
                "        - to: default",
                f"          via: {router['wan_gateway']}",
                "      nameservers:",
                f"        addresses: [{router.get('wan_dns', '1.1.1.1')}]",
            ]
        emitted_ethernets.add(wan)

    wan_vlan_id = int(wan.rsplit(".", 1)[1]) if wan_is_vlan else None
    wan_parent  = wan.rsplit(".", 1)[0] if wan_is_vlan else None

    # Trunk parent interfaces (carriers for tagged VLAN subinterfaces)
    trunk_parents = {v["interface"] for v in tagged_vlans}
    if wan_is_vlan:
        trunk_parents.add(wan_parent)
    else:
        trunk_parents.discard(wan)

    for parent in sorted(trunk_parents):
        if parent in untagged_by_if:
            # This NIC's own (untagged/native) traffic is a real network too
            # — its address goes directly on the parent, same as it would if
            # the NIC carried no tagged VLANs at all.
            v = untagged_by_if[parent]
            lines += [
                f"    {parent}:",
                f"      addresses: [{v['ip_address']}/{v['prefix_len']}]",
                "      dhcp4: false",
            ]
        elif mgmt_enabled and parent == mgmt_if:
            lines += [
                f"    {parent}:",
                f"      addresses: [{mgmt_ip}/{mgmt_prefix}]",
                "      dhcp4: false",
            ]
        else:
            lines.append(f"    {parent}: {{}}")
        emitted_ethernets.add(parent)

    # Untagged (bare physical-port) networks not already emitted above as a
    # trunk parent's own address
    for vlan in untagged_vlans:
        ifname = vlan["interface"]
        if ifname in emitted_ethernets:
            continue
        lines += [
            f"    {ifname}:",
            f"      addresses: [{vlan['ip_address']}/{vlan['prefix_len']}]",
            "      dhcp4: false",
        ]
        emitted_ethernets.add(ifname)

    # Management interface on a dedicated port. Only when mgmt_if is itself a
    # bare physical interface — a tagged management VLAN (mgmt_if containing
    # a dot, e.g. "eth1.99") gets its address from its own VlanConfig entry
    # in the vlans: section below instead, same as any other tagged VLAN.
    if mgmt_enabled and "." not in mgmt_if and mgmt_if not in emitted_ethernets:
        lines += [
            f"    {mgmt_if}:",
            f"      addresses: [{mgmt_ip}/{mgmt_prefix}]",
            "      dhcp4: false",
        ]
        emitted_ethernets.add(mgmt_if)

    # VLAN subinterfaces (tagged only — untagged physical networks were
    # already emitted above under ethernets:)
    has_vlans   = bool(tagged_vlans) or wan_is_vlan
    if has_vlans:
        lines += ["", "  vlans:"]
        # WAN VLAN subinterface (router-on-a-stick)
        if wan_is_vlan:
            if wan_mode == "dhcp":
                lines += [
                    f"    {wan}:",
                    f"      id: {wan_vlan_id}",
                    f"      link: {wan_parent}",
                    "      dhcp4: true",
                ]
            elif wan_mode == "static":
                lines += [
                    f"    {wan}:",
                    f"      id: {wan_vlan_id}",
                    f"      link: {wan_parent}",
                    f"      addresses: [{router['wan_ip']}/{router['wan_prefix']}]",
                    "      dhcp4: false",
                    "      routes:",
                    "        - to: default",
                    f"          via: {router['wan_gateway']}",
                    "      nameservers:",
                    f"        addresses: [{router.get('wan_dns', '1.1.1.1')}]",
                ]
        # LAN VLANs (skip WAN VLAN if it's in the vlans array)
        for vlan in tagged_vlans:
            subif     = f"{vlan['interface']}.{vlan['vlan_id']}"
            
            # Skip if this is the WAN VLAN (already handled above)
            if subif == wan:
                continue
            
            # Skip if no IP address (WAN VLAN marker)
            if not vlan.get('ip_address'):
                continue
            
            vlan_routes = [r for r in routes if r.get("interface") == subif]

            lines += [
                f"    {subif}:",
                f"      id: {vlan['vlan_id']}",
                f"      link: {vlan['interface']}",
                f"      addresses: [{vlan['ip_address']}/{vlan['prefix_len']}]",
                "      dhcp4: false",
            ]
            if vlan_routes:
                lines.append("      routes:")
                for route in vlan_routes:
                    lines += [
                        f"        - to: {route['destination']}",
                        f"          via: {route['gateway']}",
                    ]

    # Wireless AP bridges
    # Each SSID gets a Linux bridge that ties the virtual AP interface to the
    # VLAN subinterface, so wireless clients land on the correct VLAN.
    vap_list = hostapd_gen.vap_interfaces(state)
    if vap_list:
        lines += ["", "  bridges:"]
        for vap in vap_list:
            lines += [
                f"    {vap['bridge']}:",
                f"      interfaces: [{vap['vap']}, {vap['vlan_if']}]",
                "      dhcp4: false",
                "      parameters:",
                "        stp: false",
                "        forward-delay: 0",
            ]

    return "\n".join(lines) + "\n"
