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

    lines = ["network:", "  version: 2", "  renderer: networkd", "", "  ethernets:"]

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

    # Trunk parent interfaces (carriers for VLAN subinterfaces)
    trunk_parents = {v["interface"] for v in vlans}
    if wan_is_vlan:
        trunk_parents.add(wan.rsplit(".", 1)[0])
    else:
        trunk_parents.discard(wan)

    for parent in sorted(trunk_parents):
        if mgmt_enabled and parent == mgmt_if:
            lines += [
                f"    {parent}:",
                f"      addresses: [{mgmt_ip}/{mgmt_prefix}]",
                "      dhcp4: false",
            ]
        else:
            lines.append(f"    {parent}: {{}}")

    # Management interface on a dedicated port (not a trunk parent, not WAN)
    if mgmt_enabled and mgmt_if not in trunk_parents:
        wan_if = wan_parent if wan_is_vlan else wan
        if mgmt_if != wan_if:
            lines += [
                f"    {mgmt_if}:",
                f"      addresses: [{mgmt_ip}/{mgmt_prefix}]",
                "      dhcp4: false",
            ]

    # VLAN subinterfaces
    wan_vlan_id = int(wan.rsplit(".", 1)[1]) if wan_is_vlan else None
    wan_parent  = wan.rsplit(".", 1)[0] if wan_is_vlan else None
    has_vlans   = bool(vlans) or wan_is_vlan
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
        # LAN VLANs
        for vlan in vlans:
            subif     = f"{vlan['interface']}.{vlan['vlan_id']}"
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
