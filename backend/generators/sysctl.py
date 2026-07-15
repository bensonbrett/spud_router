# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Sysctl drop-in generator: /etc/sysctl.d/99-spud-router.conf.

Split out of generators/iptables.py (#184) so a sysctl-only change is
distinguishable from a firewall change — this is what lets the OTA guarded
auto-apply (apply_core.activate_safe_subset()) activate *only* this file
and nothing else, since neither setting can affect management reachability
(both only widen capability — enable forwarding, allow unprivileged ICMP
ping sockets — never drop SSH, change an IP/route, or firewall anyone out).
"""


def generate(state: dict) -> str:
    """
    Return the contents of /etc/sysctl.d/99-spud-router.conf:
      - net.ipv4.ip_forward = 1 — required for the router to forward
        traffic between VLANs/WAN at all.
      - net.ipv4.ping_group_range = 0 2147483647 — lets the unprivileged
        spud-router service (no CAP_NET_RAW) issue diagnostic pings via an
        unprivileged ICMP "ping socket" (SOCK_DGRAM), no setuid binary needed.

    Both are unconditional (always-on today) — `state` is accepted for
    signature consistency with every other generator and for future
    conditional sysctls.
    """
    return (
        "net.ipv4.ip_forward = 1\n"
        "net.ipv4.ping_group_range = 0 2147483647\n"
    )
