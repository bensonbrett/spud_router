# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Behavioral firewall tests (issue #174).

test_generators_iptables.py only asserts that the generated bash script
*contains* specific rule strings. It never applies the ruleset to a real
kernel or checks packet behavior — and #164/#170 both shipped with fully
green unit tests because the generated text was plausible but the runtime
behavior was wrong (Linux's weak host model, and an ordering race with
tailscaled's own iptables rules, respectively).

This tier applies the REAL generated `iptables.sh` inside real Linux
network namespaces connected by veth pairs — including a simulated
`tailscale0` interface with tailscaled's typical `ts-input` INPUT jump
installed — and sends real ICMP/TCP packets, asserting on actual outcomes.

Two verification strategies are used, matched to what's being proven:
  - "should be ALLOWED": prove a real packet gets through (ping/connect
    succeeds) — the strongest possible positive assertion.
  - "should be BLOCKED": prove the *specific intended rule* fired, by
    reading its packet counter (`iptables -L -v -n -x`) before/after. Ping
    failing alone doesn't distinguish "firewall blocked it" from "no route
    existed" — the counter does.

Requires CAP_NET_ADMIN (root) plus unshare/nsenter/ip/iptables/ping.
Everything is gated behind `_netns_available()` so `pytest tests/ -q` skips
cleanly on an unprivileged machine; CI runs this file under sudo (see
.github/workflows/ci.yml) so it's actually exercised there.

Root-owned real files are never touched: network namespaces virtualize
`/proc/sys/net/*` (safe to write) but NOT the filesystem, so the two blocks
in the generated script that persist to `/etc/sysctl.d/...` and
`/etc/iptables/rules.v4` are stripped before execution (_sanitize_for_netns)
— everything else, every `$IPT` invocation, runs completely unmodified.
"""
import ipaddress
import itertools
import os
import shutil
import subprocess
import time

import pytest

from generators.iptables import generate

pytestmark = pytest.mark.netns


def _netns_available() -> bool:
    if os.geteuid() != 0:
        return False
    return all(shutil.which(t) for t in ("unshare", "nsenter", "ip", "iptables", "ping"))


skip_no_netns = pytest.mark.skipif(
    not _netns_available(),
    reason="behavioral firewall tests need root + unshare/nsenter/ip/iptables/ping",
)


def _sanitize_for_netns(script: str) -> str:
    """
    Strip the two blocks that write real (non-namespaced) host files — the
    /etc/sysctl.d persistence heredoc and iptables-persistent's mkdir +
    iptables-save — since a network namespace shares the real filesystem
    with the host; only net.* kernel state is virtualized. Every $IPT
    invocation and the two /proc/sys/net/ipv4/... writes (genuinely
    netns-scoped) are kept byte-for-byte.
    """
    out = []
    skip_heredoc = False
    for line in script.splitlines():
        if skip_heredoc:
            if line.strip() == 'EOF':
                skip_heredoc = False
            continue
        if line.startswith("cat > /etc/sysctl.d/"):
            skip_heredoc = True
            continue
        if line.startswith("mkdir -p /etc/iptables"):
            continue
        if line.startswith("iptables-save >"):
            continue
        out.append(line)
    return "\n".join(out)


class NetnsHost:
    """
    One isolated network namespace, backed by a long-lived placeholder
    process (`unshare --net sleep <ttl>`) so nsenter has a stable /proc/<pid>
    to attach to. Deliberately NOT using named namespaces (`ip netns add`)
    — that requires a writable /var/run/netns, which isn't guaranteed (e.g.
    some containerized CI images mount /var/run read-only); PID-based
    attachment via nsenter needs nothing beyond CAP_NET_ADMIN.
    """

    def __init__(self, ttl: int = 120):
        self.proc = subprocess.Popen(
            ["unshare", "--net", "sleep", str(ttl)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            if os.path.exists(f"/proc/{self.proc.pid}/ns/net"):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("network namespace never appeared")

    @property
    def pid(self) -> int:
        return self.proc.pid

    def run(self, *args, check=True, timeout=10, input=None):
        return subprocess.run(
            ["nsenter", f"--net=/proc/{self.pid}/ns/net", *args],
            capture_output=True, text=True, check=check, timeout=timeout, input=input,
        )

    def run_script(self, script: str, check=True, timeout=15):
        return subprocess.run(
            ["nsenter", f"--net=/proc/{self.pid}/ns/net", "bash", "-s"],
            input=script, capture_output=True, text=True, check=check, timeout=timeout,
        )

    def up(self, iface: str, cidr: str) -> None:
        # `iface` was moved into this namespace by an atomic
        # `ip link set <tmp> netns <pid> name <iface>` issued from the
        # runner's namespace (#212). The device exists, but under CI load the
        # async netns handoff may not have settled by the time THIS separate
        # nsenter process first touches it, so the first `ip addr add` can fail
        # transiently — the same class of race as #212, one step later (#233).
        # Retry first use until the device is usable, tolerating an address a
        # prior attempt already applied.
        self._addr_add_when_ready(iface, cidr)
        self.run("ip", "link", "set", iface, "up")
        self.run("ip", "link", "set", "lo", "up")

    def _addr_add_when_ready(self, iface: str, cidr: str,
                             attempts: int = 40, delay: float = 0.05) -> None:
        last = None
        for _ in range(attempts):
            proc = self.run("ip", "addr", "add", cidr, "dev", iface, check=False)
            if proc.returncode == 0:
                return
            # A previous attempt already assigned the address (the add
            # succeeded but this process saw a transient error first).
            if "File exists" in proc.stderr:
                return
            last = proc
            time.sleep(delay)
        # Exhausted retries — raise the real error as check=True would have.
        raise subprocess.CalledProcessError(
            last.returncode, last.args, output=last.stdout, stderr=last.stderr,
        )

    def ping(self, dest: str, count: int = 1, timeout_s: int = 2) -> bool:
        proc = self.run("ping", "-c", str(count), "-W", str(timeout_s), dest, check=False)
        return proc.returncode == 0

    def tcp_connect(self, host: str, port: int, timeout_s: int = 2) -> bool:
        """Best-effort TCP reachability check using bash's /dev/tcp — avoids
        depending on nc/curl being installed inside the namespace."""
        proc = self.run(
            "bash", "-c",
            f"timeout {timeout_s} bash -c 'echo > /dev/tcp/{host}/{port}'",
            check=False,
        )
        return proc.returncode == 0

    def rule_hits(self, table: str, chain: str, match_substr: str) -> int:
        """Sum packet counts (`-v -n -x`) across every rule in `chain`
        whose text contains `match_substr` — the counter-based proof that a
        specific rule (not just "some" rule) actually fired."""
        args = ["iptables", "-t", table, "-L", chain, "-v", "-n", "-x"] if table != "filter" \
            else ["iptables", "-L", chain, "-v", "-n", "-x"]
        proc = self.run(*args, check=False)
        total = 0
        for line in proc.stdout.splitlines():
            if match_substr in line:
                parts = line.split()
                if len(parts) >= 2 and parts[0].isdigit():
                    total += int(parts[0])
        return total

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


_veth_counter = itertools.count()


def _veth(name_a: str, host_a: NetnsHost, name_b: str, host_b: NetnsHost) -> None:
    """
    Create a veth pair and move+rename each end into its target host's
    namespace in ONE atomic netlink operation — `ip link set <tmp> netns
    <pid> name <final>` — issued from the runner's own namespace. The
    initial create happens under scratch names in the CURRENT (test-runner)
    namespace — using the desired final names directly there would collide
    with any real interface of the same name already on the host running
    this test (e.g. a real "tailscale0" on a machine actually running
    Tailscale, or a real "eth0"/"eth1").

    This used to be a separate move (`ip link set <tmp> netns <pid>`)
    followed by a rename issued from *inside* the target namespace via a
    second nsenter (`_rename_when_visible`, removed — #212). That two-step
    sequence raced the async netns handoff: a brief window existed where a
    separate nsenter process hadn't yet observed the device, so the rename
    could spuriously fail under CI load, and no amount of retrying made it
    less racy, just less likely to be caught. Folding the rename into the
    SAME command as the move eliminates the race outright — the kernel
    performs both as a single operation, so there's no window where a
    second process needs to observe an intermediate state.
    """
    n = next(_veth_counter)
    tmp_a, tmp_b = f"vt{n}a", f"vt{n}b"
    subprocess.run(["ip", "link", "add", tmp_a, "type", "veth", "peer", "name", tmp_b], check=True)
    subprocess.run(["ip", "link", "set", tmp_a, "netns", str(host_a.pid), "name", name_a], check=True)
    subprocess.run(["ip", "link", "set", tmp_b, "netns", str(host_b.pid), "name", name_b], check=True)


def _apply(router: NetnsHost, state: dict) -> None:
    """Generate the real ruleset for `state` and apply it, sanitized, inside
    the router's namespace. Custom chains (e.g. a fake ts-input) don't
    survive `$IPT -X` if non-empty/referenced, so callers that need
    tailscaled-jump simulation must (re)install it AFTER calling this.

    #184 split ip_forward/ping_group_range out of generators/iptables.py
    into generators/sysctl.py, which only produces a sysctl.d *file* — it no
    longer emits the live `/proc/sys/net/ipv4/...` writes this tier used to
    get for free from the generated script. FORWARD-chain behavior (VLAN-to-
    VLAN, WAN masquerade) still needs ip_forward=1 live in each namespace, so
    set it directly here — /proc/sys/net/ipv4/* is netns-scoped (safe to
    write) same as the sanitizer's existing rationale for skipping the real
    (non-namespaced) file writes."""
    router.run("bash", "-c", "echo 1 > /proc/sys/net/ipv4/ip_forward")
    script = _sanitize_for_netns(generate(state))
    router.run_script(script)


def _install_fake_tailscaled_jump(router: NetnsHost) -> None:
    """
    Mimic tailscaled's own independent iptables management: a `ts-input`
    chain that unconditionally accepts everything arriving on tailscale0,
    jumped to from the very TOP of INPUT — issue #170's exact bug scenario
    (tailscaled re-inserts this jump asynchronously and it can land ahead of
    spud-router's own rules). The fix must not depend on where in INPUT
    this jump lands, since spud-router doesn't control tailscaled's
    ordering — see generators/iptables.py's raw-table PREROUTING DROP.
    """
    router.run("iptables", "-N", "ts-input", check=False)  # tolerate re-run
    router.run("iptables", "-F", "ts-input")
    router.run("iptables", "-A", "ts-input", "-i", "tailscale0", "-j", "ACCEPT")
    router.run("iptables", "-D", "INPUT", "-j", "ts-input", check=False)  # avoid dupes
    router.run("iptables", "-I", "INPUT", "1", "-j", "ts-input")


# ── Shared topology ──────────────────────────────────────────────────────────
#
#            client_mgmt --- vmgmt  [router]  vwan --- client_wan (the "internet")
#                                       |
#                              vp0.10 --+-- vp0.20
#                                |             |
#                          client_lan     client_iot
#                                       |
#                                  tailscale0 --- client_ts (a tailnet peer)
#
# Router-side IPs match what each test's state configures as mgmt_ip /
# VLAN ip_address, so the generator's -d <ip> rules are meaningful.

MGMT_ROUTER_IP, MGMT_CLIENT_IP = "10.99.0.1", "10.99.0.2"
LAN_ROUTER_IP,  LAN_CLIENT_IP  = "10.99.10.1", "10.99.10.2"
IOT_ROUTER_IP,  IOT_CLIENT_IP  = "10.99.20.1", "10.99.20.2"
WAN_ROUTER_IP,  WAN_CLIENT_IP  = "10.99.254.1", "10.99.254.2"
TS_ROUTER_IP,   TS_CLIENT_IP   = "10.99.100.1", "10.99.100.2"


@pytest.fixture
def topology():
    """Fresh router + 5 peer namespaces per test — simplest way to guarantee
    no iptables/veth state leaks between tests (a shared/module-scoped
    topology would need to reason about custom-chain survival across each
    test's `$IPT -F`/`-X`, which is fragile)."""
    router = NetnsHost()
    client_mgmt = NetnsHost()
    client_lan = NetnsHost()
    client_iot = NetnsHost()
    client_wan = NetnsHost()
    client_ts = NetnsHost()

    hosts = [router, client_mgmt, client_lan, client_iot, client_wan, client_ts]
    try:
        # Peer names must be unique and NOT collide with any real interface
        # on the host running this test (e.g. its actual "eth0") — veth
        # creation happens in the CURRENT namespace before either end is
        # moved, so a name matching something already there fails outright.
        _veth("vmgmt", router, "cmgmt0", client_mgmt)
        _veth("vp0.10", router, "clan0", client_lan)
        _veth("vp0.20", router, "ciot0", client_iot)
        _veth("vwan", router, "cwan0", client_wan)
        _veth("tailscale0", router, "cts0", client_ts)

        router.up("vmgmt", f"{MGMT_ROUTER_IP}/24")
        router.up("vp0.10", f"{LAN_ROUTER_IP}/24")
        router.up("vp0.20", f"{IOT_ROUTER_IP}/24")
        router.up("vwan", f"{WAN_ROUTER_IP}/30")
        router.up("tailscale0", f"{TS_ROUTER_IP}/24")

        client_mgmt.up("cmgmt0", f"{MGMT_CLIENT_IP}/24")
        client_lan.up("clan0", f"{LAN_CLIENT_IP}/24")
        client_iot.up("ciot0", f"{IOT_CLIENT_IP}/24")
        client_wan.up("cwan0", f"{WAN_CLIENT_IP}/30")
        client_ts.up("cts0", f"{TS_CLIENT_IP}/24")

        # Round-trip routes for cross-subnet scenarios (inter-VLAN, tailnet
        # path) so a blocked ping fails because of the FIREWALL, not because
        # a reply had nowhere to go.
        client_lan.run("ip", "route", "add", f"{IOT_ROUTER_IP}/32", "via", LAN_ROUTER_IP, check=False)
        client_lan.run("ip", "route", "add", f"{IOT_CLIENT_IP}/32", "via", LAN_ROUTER_IP, check=False)
        client_iot.run("ip", "route", "add", f"{LAN_ROUTER_IP}/32", "via", IOT_ROUTER_IP, check=False)
        client_iot.run("ip", "route", "add", f"{LAN_CLIENT_IP}/32", "via", IOT_ROUTER_IP, check=False)
        client_ts.run("ip", "route", "add", f"{MGMT_ROUTER_IP}/32", "via", TS_ROUTER_IP, check=False)
        client_ts.run("ip", "route", "add", f"{LAN_ROUTER_IP}/32", "via", TS_ROUTER_IP, check=False)
        client_wan.run("ip", "route", "add", f"{LAN_CLIENT_IP}/32", "via", WAN_ROUTER_IP, check=False)
        # client_lan -> mgmt IP is LOCAL DELIVERY on the router (both subnets
        # are directly attached to it — no FORWARD rule involved at all,
        # since the destination is the router's own address). This route is
        # only so client_lan's packet reaches the router in the first place;
        # the reply path back is automatic (directly connected).
        client_lan.run("ip", "route", "add", f"{MGMT_ROUTER_IP}/32", "via", LAN_ROUTER_IP, check=False)
        # DNAT reachability needs a real round trip: the LAN host's SYN-ACK
        # is addressed to the real WAN client IP (conntrack un-DNATs it back
        # through the router), so client_lan needs a route there at all.
        client_lan.run("ip", "route", "add", f"{WAN_CLIENT_IP}/32", "via", LAN_ROUTER_IP, check=False)

        yield {
            "router": router, "client_mgmt": client_mgmt, "client_lan": client_lan,
            "client_iot": client_iot, "client_wan": client_wan, "client_ts": client_ts,
        }
    finally:
        for h in hosts:
            h.close()


def _base_state() -> dict:
    return {
        "router": {
            "wan_interface": "vwan", "wan_mode": "dhcp",
            "mgmt_enabled": True, "mgmt_interface": "vmgmt", "mgmt_ip": MGMT_ROUTER_IP,
            "mgmt_icmp_echo": False,
        },
        "vlans": [
            {"vlan_id": 10, "name": "LAN", "interface": "vp0", "ip_address": LAN_ROUTER_IP,
             "prefix_len": 24, "dhcp_enabled": True, "dhcp_start": "10.99.10.100",
             "dhcp_end": "10.99.10.200", "dhcp_lease": "12h", "isolate": False, "icmp_echo": False},
            {"vlan_id": 20, "name": "IoT", "interface": "vp0", "ip_address": IOT_ROUTER_IP,
             "prefix_len": 24, "dhcp_enabled": True, "dhcp_start": "10.99.20.100",
             "dhcp_end": "10.99.20.200", "dhcp_lease": "12h", "isolate": True, "icmp_echo": False},
        ],
        "static_routes": [], "dns_entries": [], "fw_inbound": [], "fw_intervlan": [],
        "tailscale": {"enabled": True, "advertise_routes": [], "exit_node": False, "accept_routes": True},
    }


@skip_no_netns
class TestPingToggle:
    """The #164/#170 scenarios verbatim: a ping-toggle-off IP must be
    unreachable from every arrival path, including via a simulated
    tailscale0 with tailscaled's own top-of-INPUT accept-all jump."""

    def test_mgmt_ping_off_blocks_direct_path(self, topology):
        state = _base_state()
        _apply(topology["router"], state)
        assert topology["client_mgmt"].ping(MGMT_ROUTER_IP) is False
        assert topology["router"].rule_hits("raw", "PREROUTING", MGMT_ROUTER_IP) >= 1

    def test_mgmt_ping_off_blocks_from_other_vlan(self, topology):
        state = _base_state()
        _apply(topology["router"], state)
        # LAN (VLAN10) client pinging the mgmt IP — a different arrival
        # interface than the mgmt toggle's own, exactly the weak-host-model
        # bypass #164 fixed. This is local INPUT delivery (the destination
        # is the router's own address, both subnets directly attached) —
        # no FORWARD rule is involved, which is exactly why the old
        # interface-scoped rule used to miss it.
        assert topology["client_lan"].ping(MGMT_ROUTER_IP) is False
        assert topology["router"].rule_hits("raw", "PREROUTING", MGMT_ROUTER_IP) >= 1

    def test_mgmt_ping_off_blocks_via_tailscale_path(self, topology):
        """Issue #170: with tailscaled's ts-input jump inserted at the TOP
        of INPUT (maximally advantaged), the raw-table DROP must still win
        because raw PREROUTING is an earlier netfilter hook than INPUT."""
        state = _base_state()
        _apply(topology["router"], state)
        _install_fake_tailscaled_jump(topology["router"])
        assert topology["client_ts"].ping(MGMT_ROUTER_IP) is False
        assert topology["router"].rule_hits("raw", "PREROUTING", MGMT_ROUTER_IP) >= 1

    def test_mgmt_ping_on_allows_all_paths(self, topology):
        state = _base_state()
        state["router"]["mgmt_icmp_echo"] = True
        _apply(topology["router"], state)
        _install_fake_tailscaled_jump(topology["router"])
        assert topology["client_mgmt"].ping(MGMT_ROUTER_IP) is True
        assert topology["client_lan"].ping(MGMT_ROUTER_IP) is True
        assert topology["client_ts"].ping(MGMT_ROUTER_IP) is True

    def test_vlan_ping_off_blocks_via_tailscale_path(self, topology):
        state = _base_state()
        state["vlans"][0]["icmp_echo"] = False
        _apply(topology["router"], state)
        _install_fake_tailscaled_jump(topology["router"])
        assert topology["client_ts"].ping(LAN_ROUTER_IP) is False
        assert topology["router"].rule_hits("raw", "PREROUTING", LAN_ROUTER_IP) >= 1

    def test_vlan_ping_on_allows(self, topology):
        state = _base_state()
        state["vlans"][0]["icmp_echo"] = True
        _apply(topology["router"], state)
        assert topology["client_lan"].ping(LAN_ROUTER_IP) is True


@skip_no_netns
class TestWanDefaultDeny:
    def test_wan_inbound_unsolicited_blocked(self, topology):
        state = _base_state()
        _apply(topology["router"], state)
        # No port-forward configured — an inbound connection attempt from
        # the "internet" to any WAN-side port must fail (default DROP).
        assert topology["client_wan"].tcp_connect(WAN_ROUTER_IP, 8443) is False


@skip_no_netns
class TestInterVlanIsolation:
    def test_isolated_vlan_blocks_forward(self, topology):
        state = _base_state()  # VLAN20 isolate=True already
        _apply(topology["router"], state)
        assert topology["client_lan"].ping(IOT_CLIENT_IP) is False

    def test_non_isolated_vlan_allows_forward(self, topology):
        state = _base_state()
        state["vlans"][1]["isolate"] = False
        _apply(topology["router"], state)
        assert topology["client_lan"].ping(IOT_CLIENT_IP) is True


@skip_no_netns
class TestPortForwardDnat:
    def test_forwarded_port_reaches_lan_host(self, topology):
        state = _base_state()
        state["port_forwards"] = [{
            "id": "t1", "proto": "tcp", "wan_port": 8443,
            "lan_host": LAN_CLIENT_IP, "lan_port": 22, "description": "", "enabled": True,
        }]
        _apply(topology["router"], state)
        # A tiny listener on the "LAN client" so the DNAT'd connection has
        # something to complete a TCP handshake with.
        topology["client_lan"].run(
            "bash", "-c",
            "(nohup bash -c 'while true; do echo hi | timeout 2 nc -l -p 22 -q1 2>/dev/null || "
            "timeout 2 python3 -m http.server 22 >/dev/null 2>&1; done') >/dev/null 2>&1 &",
            check=False,
        )
        time.sleep(0.3)
        assert topology["client_wan"].tcp_connect(WAN_ROUTER_IP, 8443) is True

    def test_forward_not_reachable_when_disabled(self, topology):
        state = _base_state()
        state["port_forwards"] = [{
            "id": "t2", "proto": "tcp", "wan_port": 8443,
            "lan_host": LAN_CLIENT_IP, "lan_port": 22, "description": "", "enabled": False,
        }]
        _apply(topology["router"], state)
        assert topology["client_wan"].tcp_connect(WAN_ROUTER_IP, 8443) is False


@skip_no_netns
class TestDohBlockWanDns:
    def test_block_wan_dns_blocks_routers_own_plaintext_dns(self, topology):
        state = _base_state()
        state["router"]["wan_dns_mode"] = "doh"
        state["router"]["doh_provider"] = "cloudflare"
        state["router"]["block_wan_dns"] = True
        _apply(topology["router"], state)
        # The router's own OUTPUT to a plaintext DNS server on WAN must be
        # rejected/dropped — simulate by asking the router netns to reach
        # the "upstream" client on :53 and confirming the REJECT rule fired.
        topology["router"].run("bash", "-c", f"timeout 2 bash -c 'echo > /dev/udp/{WAN_CLIENT_IP}/53'", check=False)
        assert topology["router"].rule_hits("filter", "OUTPUT", "dpt:53") >= 1

    def test_no_block_when_disabled(self, topology):
        state = _base_state()
        state["router"]["wan_dns_mode"] = "auto"
        state["router"]["block_wan_dns"] = False
        _apply(topology["router"], state)
        assert topology["router"].rule_hits("filter", "OUTPUT", "dpt:53") == 0
