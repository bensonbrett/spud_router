# 🥔 spud-router - AI-Enabled Router

An open-source router that you can manage from a browser, a terminal CLI, or **directly from your AI agents** (Claude Desktop, OpenCode, Cline, GitHub Copilot). Born as a router-on-a-stick for the [Le Potato](https://libre.computer/products/aml-s905x-cc/) (or any ARM SBC running Armbian/Ubuntu), it also installs and runs on x86 — mini-PCs, thin clients, and VMs — where it adapts the network topology to however many NICs the box actually has. Manages 802.1Q VLANs, DHCP, DNS, firewall rules, static routes, and VPN (Tailscale, WireGuard, Nebula) — all from a browser, over SSH, or through a standard MCP server. Still a potato at heart either way.

<table>
  <tr>
    <td width="33%"><img src="docs/images/web-ui.png" alt="spud-router Web UI"><br><em>Web UI — browser-based management</em></td>
    <td width="33%"><img src="docs/images/tui.png" alt="spud-router TUI"><br><em>TUI — full-featured CLI over SSH</em></td>
    <td width="33%"><img src="docs/images/mcp.png" alt="spud-router MCP"><br><em>MCP — AI agent integration</em></td>
  </tr>
</table>

<details>
<summary>📸 Click to view screenshots of every tab and TUI screen</summary>

### Web UI

| Tab | Screenshot |
|-----|------------|
| VLANs | ![VLANs](docs/images/vlans.png) |
| WAN | ![WAN](docs/images/wan.png) |
| DNS | ![DNS](docs/images/dns.png) |
| Routes | ![Routes](docs/images/routes.png) |
| Firewall | ![Firewall](docs/images/firewall.png) |
| VPN | ![VPN](docs/images/vpn.png) |
| Wireless | ![Wireless](docs/images/wireless.png) |
| Diagnostics | ![Diagnostics](docs/images/diagnostics.png) |
| Logging | ![Logging](docs/images/logging.png) |
| SNMP | ![SNMP](docs/images/snmp.png) |
| Status | ![Status](docs/images/status.png) |
| Preview | ![Preview](docs/images/preview.png) |
| Update | ![Update](docs/images/update.png) |
| Settings | ![Settings](docs/images/settings.png) |

### TUI (spud-cli)

| Screen | Screenshot |
|--------|------------|
| VLANs | ![tui-vlans](docs/images/tui-vlans.png) |
| WAN | ![tui-wan](docs/images/tui-wan.png) |
| DNS | ![tui-dns](docs/images/tui-dns.png) |
| Routes | ![tui-routes](docs/images/tui-routes.png) |
| Firewall | ![tui-firewall](docs/images/tui-firewall.png) |
| VPN | ![tui-vpn](docs/images/tui-vpn.png) |
| Wireless | ![tui-wireless](docs/images/tui-wireless.png) |
| Syslog | ![tui-syslog](docs/images/tui-syslog.png) |
| SNMP | ![tui-snmp](docs/images/tui-snmp.png) |
| Status | ![tui-status](docs/images/tui-status.png) |

</details>

> **⚠️ Disclaimer:** Let's be real: this entire project was coded by AI. If you came here expecting a pristine, enterprise-grade, hyper-optimized networking masterpiece... you're in the wrong place, but it's closer than it has any right to be.
> 
> I built it because I needed a router for a simple use case on a 100 Mbps Starlink plan. I had a potato lying around and an idea to write a quick script using standard Linux networking commands. Then I took that idea way, way too far. What started as "just get VLANs working" became "why not add WireGuard, Nebula, port forwarding, DNS-over-HTTPS, SNMP, remote syslog, system monitoring, OTA updates with auto-rollback, and a web UI?" I had more ideas than credits, but somehow we made it here.
> 
> It's still a potato. But it's a potato that can do things. I even had a decent model check it over for security — it found some things, and it fixed them. So we've got that going for us.
> 
> Update: I deployed the potato for real over the weekend. It came right up on Starlink and Tailscale, and has been happily handling multiple people streaming music plus all the usual internet traffic without complaint. So it's no longer just "running on my bench" — it's actually out in the field doing the job. I hope it works just as well for anyone who tries it.
> 
> If it doesn't work? Submit an issue and I'll have an AI agent read it, probably mess it up three times, then have a slightly better model patch up this beautiful disaster. At least the story's good: a guy, a potato, and a cast of AI agents that refused to quit.

> **🧪 Untested Features:** The following features have not yet been verified on real hardware. Each has a tracking issue with a concrete test checklist — verification is queued behind a hardware test device ([`needs-hardware`](https://github.com/bensonbrett/spud_router/issues?q=is%3Aissue+is%3Aopen+label%3Aneeds-hardware)):
> - **WireGuard** — hub/server and client modes, peer management, key generation ([#199](https://github.com/bensonbrett/spud_router/issues/199))
> - **Nebula** — overlay mesh networking, cert import, firewall rules ([#200](https://github.com/bensonbrett/spud_router/issues/200))
> - **SNMP** — Net-SNMP agent with community strings and allowlists ([#201](https://github.com/bensonbrett/spud_router/issues/201))
> - **Wireless** — hostapd-based AP with multiple SSIDs and VLAN bridging ([#202](https://github.com/bensonbrett/spud_router/issues/202))
> - **Remote syslog** — log forwarding via UDP, TCP, or TLS ([#203](https://github.com/bensonbrett/spud_router/issues/203))

---

## Features

### 🤖 AI Agent Integration (MCP Server)

spud-router ships with a **Model Context Protocol (MCP) server** that runs on your machine and connects to your router via API key. Any MCP-compatible client can inspect, configure, and manage your router in real time.

- **38 tools** for reading and managing your router — list VLANs, view firewall rules, check VPN status, add routes, configure DNS, and more.
- **Standard MCP protocol** — works with Claude Desktop, OpenCode, VS Code Cline, GitHub Copilot, and any other MCP client out of the box.
- **Runs on your machine** — install with `pip install` and connect with a single command. No SSH needed. The MCP server authenticates via Bearer token (API key).
- **Transactional staging pipeline** — staged config changes go through `begin → op → validate → commit → confirm` with auto-revert safety timer. Some diagnostic and provider-specific tools call the backend API directly.
- **Read-only mode** — limit agents to read-only tools for security. Configure per-key.
- **One-click setup** — generate an API key from the web UI or TUI, then use the ready-to-copy command to connect any MCP client.

**Quick start:**
```bash
pip install git+https://github.com/bensonbrett/spud_router.git#subdirectory=backend
spud-router-mcp --api-key <your-key> --base-url https://<router-ip>:8080
```

You can also put the connection settings in a JSON file and point clients at it:

```json
{
  "api_key": "spud_...",
  "base_url": "https://192.168.10.1:8080",
  "tls_verify": false,
  "read_only": false
}
```

```bash
spud-router-mcp --config ~/.config/spud-router/mcp.json
```

**Client configuration:**

<details>
<summary><strong>OpenCode</strong> (<code>~/.config/opencode/opencode.jsonc</code>)</summary>

```json
{
  "mcp": {
    "spud-router": {
      "type": "local",
      "command": ["spud-router-mcp", "--config", "/home/you/.config/spud-router/mcp.json"],
      "enabled": true
    }
  }
}
```
</details>

<details>
<summary><strong>Claude Desktop</strong> (<code>claude_desktop_config.json</code>)</summary>

```json
{
  "mcpServers": {
    "spud-router": {
      "command": "spud-router-mcp",
      "args": ["--config", "/home/you/.config/spud-router/mcp.json"]
    }
  }
}
```
</details>

<details>
<summary><strong>VS Code Cline</strong> (<code>cline_mcp_settings.json</code>)</summary>

```json
{
  "mcpServers": {
    "spud-router": {
      "command": "spud-router-mcp",
      "args": ["--config", "/home/you/.config/spud-router/mcp.json"]
    }
  }
}
```
</details>

<details>
<summary><strong>GitHub Copilot</strong> (<code>~/.github/copilot-mcp.json</code>)</summary>

```json
{
  "spud-router": {
    "command": "spud-router-mcp",
    "args": ["--config", "/home/you/.config/spud-router/mcp.json"]
  }
}
```
</details>

<details>
<summary><strong>Codex CLI</strong> (<code>~/.codex/mcp.json</code>)</summary>

```json
{
  "mcpServers": {
    "spud-router": {
      "command": "spud-router-mcp",
      "args": ["--config", "/home/you/.config/spud-router/mcp.json"]
    }
  }
}
```
</details>

### 🌐 Networking

- **Router-on-a-stick** — 802.1Q VLAN subinterfaces on a single trunk port. One cable to a managed switch does WAN, LAN, and everything in between.
- **Multi-NIC installs** — on boards with 2+ physical ports, the installer offers WAN/LAN on separate untagged interfaces instead of a VLAN trunk (see [Install](#install)).
- **Per-VLAN DHCP** — dnsmasq scopes per VLAN with configurable range, lease time, gateway, DNS server, and custom DHCP options (NTP, etc.).
- **DHCP reservations** — pin a MAC address to a fixed IP within a VLAN's subnet, with an optional hostname; managed per-VLAN from the web UI or CLI.
- **VLAN isolation** — per-VLAN toggle to block inter-VLAN routing.
- **Static routes** — per-VLAN subinterface or global, with optional description.
- **BGP dynamic routing** — IPv4 BGP via FRR (bgpd): local ASN + router-id, neighbor peers (IP, remote-AS, description), and advertised networks; accept-all import/export (no route-maps/prefix-lists). Live session status (Established/Active/Idle, prefixes received/advertised) from the Routes tab or CLI. Opt-in — FRR stays disabled until BGP is enabled.
- **WAN** — DHCP or static IP; upstream DNS from the WAN lease, manual, or DNS-over-HTTPS (via dnsproxy).
- **Management interface** — untagged access port. Plug a laptop into the trunk port and get DHCP + web UI immediately, no switch config needed.
- **Wireless access point** — hostapd-based AP with multiple SSIDs, each bridged to a different VLAN. WPA2, WPA3, or mixed mode; 2.4/5 GHz; hidden SSIDs.

### 🛡️ Firewall

- **Inbound rules** — per-VLAN allow/drop by protocol, port, or ICMP type, with common port presets (SSH, HTTP, DNS, RDP, SMB…).
- **Inter-VLAN access matrix** — visual table of which VLANs can talk to which. Auto-mesh mode (all open by default) or explicit-only (default-deny, add rules to open holes).
- **Outbound (egress) rules** — per-VLAN allow/deny with optional destination CIDR, plus a configurable default policy (allow or deny).
- **ICMP support** — ping is a first-class protocol in all rule types, with named type presets (echo-request, destination-unreachable, etc.).
- **NAT masquerade** — SNAT/PAT on WAN so LAN clients share the WAN IP. Same treatment on every enabled VPN provider's interface, so LAN traffic forwarded onto a VPN appears from the router's VPN IP.
- **Port forwarding (DNAT)** — forward a WAN port to a host:port on the LAN (tcp/udp), with per-forward enable/disable and common port presets (HTTP, HTTPS, SSH).

### 📡 DNS

- **dnsmasq DNS server** — automatic on all LAN interfaces, with the `.lan` domain.
- **Custom A records** — add local DNS entries (e.g. `nas`, `proxmox`) resolvable across all VLANs.
- **DNS-over-HTTPS** — upstream DNS via a local `dnsproxy` instance. Built-in providers (Cloudflare, Quad9, Google) use IP-pinned endpoints so they never need a plaintext lookup; a custom URL gets a narrow bootstrap exception if it's a hostname.
- **WAN DNS block** — optionally block plaintext DNS (port 53) to WAN when DoH is active, with a fail-safe to prevent DNS outage if dnsproxy is unhealthy.

### 🔒 VPN

Three providers, each independently enabled and coexisting — mix and match. A
built-in check blocks configs where more than one would try to become the
default route for all outbound traffic.

- **Tailscale** — auth-key provisioning (write-only, file-fed), advertised routes auto-populated from your VLAN subnets plus free-text entries, exit-node mode, live peer online/offline status, and safe defaults (`--accept-dns=false` so it can't hijack the router's DNS).
- **WireGuard** — hub/server or client mode, peer CRUD with server-side keypair generation, one-time reveal of a generated peer's private key as a ready-to-use `.conf` and QR code (never persisted), and a regenerate-key action for the router's own identity key.
- **Nebula** — join-only overlay mesh: import a pre-signed host cert/key + CA cert (validated with `nebula-cert verify` and a live smoke test before anything is saved), lighthouse hosts and static host map, and its own inbound/outbound overlay firewall rules (separate from the WAN-facing iptables rules).
- **NAT masquerade** — every enabled provider gets the same INPUT/FORWARD/MASQUERADE treatment on its own interface, so LAN traffic routed through any of them appears from the router's VPN IP.

### 📋 Monitoring

- **Remote syslog** — forward logs to a remote server via UDP, TCP, or TLS, with configurable facility/severity and connectivity test.
- **SNMP agent** — Net-SNMP v2c with read-only and read-write community strings (write-only, never echoed), source IP allowlist, and bind interface.
- **Diagnostics panel** — per-interface carrier/IP status, DHCP lease attribution, PVID hints, command runner (ping/traceroute/nslookup), and Wake-on-LAN (optionally targeted at a specific VLAN's broadcast address) — all from the browser or CLI.
- **Status page** — live interfaces, routing table, and DHCP leases in both the web UI and CLI.
- **System monitoring dashboard** — memory, CPU, and load-average gauges, disk usage for `/` and `/etc/spud-router`, and per-interface (WAN + VLAN) traffic counters, polled live in the web UI and shown in the CLI status screen — read entirely from `/proc`/`/sys`, no external tools.

### ⚙️ System

- **Config preview** — view generated netplan, dnsmasq, iptables, hostapd, syslog, and SNMP config before applying.
- **Config export/import** — full state + generated configs as a zip backup; restore from JSON with validation.
- **Pending changes detection** — knows when you've made edits but haven't clicked Apply, and tells you.
- **Commit-confirmed apply** — every Apply is armed with a 90-second connectivity watchdog; if you don't click "Keep changes" (or reload and confirm) in time, it auto-reverts to the last known-good config, so a bad WAN/VLAN/route/firewall/VPN change can't permanently strand a remote admin.
- **Reboot management** — reboot from the UI or CLI with confirmation. Detects if a reboot is needed and shows a banner.
- **OTA updates** — checks GitHub for new releases, downloads with SHA256 verification, applies with backup + health-gate + auto-rollback on failure. Provisions system dependencies so new features work without re-running the installer.
- **TLS certificate management** — upload a new cert or regenerate the self-signed one from the UI.

### 🖥️ Shell CLI (spud-cli)

- **Full interactive TUI** over SSH — feature parity with the web UI. Launches automatically when the `spud` user logs in. Zero pip dependencies — pure Python stdlib.
- **SSH banner + MOTD** — ASCII art logo on connect; live status panel (WAN IP, VLAN count, leases, uptime) after login.

### 🔐 Security

- **Stateless HMAC-signed session tokens** that survive service restarts and reboots.
- **httpOnly session cookies** — no JavaScript-accessible storage. SameSite=Strict.
- **scrypt password hashing** — transparently upgrades legacy SHA-256 hashes on first login.
- **Login rate limiting** — 5 attempts per 60 seconds per IP.
- **Privilege separation** — the backend runs as an unprivileged user with granular sudoers grants, not `NOPASSWD: ALL`.
- **Self-signed TLS** out of the box — no cert-procurement step during install.

---

## Hardware

| Component | Recommendation |
|-----------|---------------|
| SBC | [Le Potato (AML-S905X-CC)](https://libre.computer/products/aml-s905x-cc/) |
| OS | [Armbian minimal](https://www.armbian.com/lepotato/) (Ubuntu 22.04 or 24.04) |
| Storage | microSD ≥ 8GB (Class 10 / A1) |
| Switch | Any 802.1Q managed switch (Netgear GS308E, TP-Link TL-SG108E, etc.) — only needed for the 1-NIC or 2-NIC-with-management-VLAN topologies below |

`install.sh` also installs and runs on x86 (mini-PCs, thin clients, VMs) with an Ubuntu 22.04/24.04-class OS — the same package list and systemd units apply, no ARM-specific assumptions in the app itself.

### Supported hardware & topologies

The installer detects how many physical NICs the box has and picks a network topology automatically — see [Install](#install) step 3 for the interactive/non-interactive details. Summary:

| NICs | Topology | Needs a managed switch? |
|------|----------|--------------------------|
| **1** | Router-on-a-stick — WAN (VLAN 2) + LAN (VLAN 10) + untagged management, all on one trunk port | Yes (802.1Q) |
| **2** | Dedicated WAN + LAN, each on its own port. Management shares the LAN network by default, or optionally rides its own tagged VLAN on the LAN port | No (flat default) / Yes (if you opt into the management VLAN) |
| **3** | WAN, LAN, and management each get a dedicated physical port — no VLANs needed at all | No |

The 1-NIC and 2-NIC tiers are hardware-tested. **The 3-NIC tier ships generation-validated only** — its `state.json`/netplan/dnsmasq/iptables output is covered by the automated test suite, but a live 3-NIC install hasn't been run on real hardware yet; treat it the same as the 🧪 Untested Features above (hardware verification pending, tracked alongside those issues).

### Management interface addressing

Whenever management has its own dedicated interface (a tagged VLAN or a dedicated physical port — the 2-NIC "yes" and 3-NIC tiers above), spud-router treats it the way real firewall appliances treat a management port: it can be a **DHCP client** on an existing management network, or **static**, but it never has to be both server and client on the same interface.

- **DHCP (default for those tiers)** — the management interface takes a lease from *your* management network's own DHCP server (pin it with a reservation so the address doesn't move). spud-router runs **no DHCP server** of its own on that interface. The lease's default route and DNS are explicitly suppressed (netplan `dhcp4-overrides: { use-routes: false, use-dns: false }`), so it can never steal the default route from WAN — the box still only ever routes outbound traffic through WAN.
- **Static** — spud-router owns the management subnet and serves its own DHCP scope on it, same as the 1-NIC router-on-a-stick tier has always done.

Either way, **SSH and the web UI bind to the management interface itself, not to an IP address** — so switching addressing modes never changes what's reachable, only how the address gets assigned. On-link management only for now: there's no management-route/gateway feature, so an admin on a *different* subnet than the management network (behind its own router) may not have a return path in DHCP mode — that's a tracked follow-up, not something to work around by hand.

---

## Install

### 1. Flash Armbian

Download Armbian minimal for Le Potato, flash to microSD, boot, and SSH in as root.

### 2. Download the latest release

```bash
curl -L "$(curl -fsSL https://api.github.com/repos/bensonbrett/spud_router/releases/latest \
  | grep browser_download_url | grep '\.tar\.gz' | head -1 | cut -d '"' -f 4)" | tar xz
```

Or download a specific version:

```bash
curl -L https://github.com/bensonbrett/spud_router/releases/download/v1.0.0/spud-router-v1.0.0.tar.gz \
  | tar xz
```

### 3. Run the installer

```bash
sudo bash install.sh
```

The installer:
- Installs system deps (`dnsmasq`, `iptables-persistent`, `hostapd`, `snmpd`, `rsyslog`, `vlan`, `netplan`, `fail2ban`, `python3`)
- Disables `NetworkManager`; runs `systemd-resolved` with its stub listener off (`DNSStubListener=no`) so dnsmasq owns port 53 while resolved still learns upstream DNS from the WAN DHCP lease
- Creates a Python venv at `/opt/spud-router/venv`
- Copies the `backend/` app and the built UI to `/opt/spud-router/`
- Prompts for admin credentials (min 12 chars)
- Enables and starts the `spud-router` systemd service
- Hardens SSH, configures fail2ban — if run while logged in as `root` directly (not via `sudo` from an unprivileged account), it will prompt for a non-root admin username to permit for SSH, so root lockout (`spud`'s shell is the TUI, not bash) can't happen silently
- Persists IP forwarding via `/etc/sysctl.d/99-spud-router.conf`
- Writes a bootstrap netplan + dnsmasq config so the management interface works immediately
- Pre-populates WAN (VLAN 2) and LAN (VLAN 10) — click Apply to activate
- Installs Tailscale, WireGuard, and Nebula (`nebula`/`nebula-cert`) — enable/configure whichever you want from the web UI; Tailscale needs `tailscale up` once to authenticate
- Installs FRR (bgpd enabled, service disabled) for BGP — enable and configure from the Routes tab or CLI

The installer detects how many physical NICs the board has and tiers the
topology accordingly (see [Supported hardware & topologies](#supported-hardware--topologies)):

- **1 NIC** (e.g. the Le Potato) → router-on-a-stick, same as always.
  On a TTY it asks **"Accept these defaults? [Enter to accept / c to
  customize]"** before writing `state.json` — Enter (or any non-interactive
  install, e.g. piping a script into `install.sh`) keeps the exact layout
  below unchanged; `c` lets you customize the LAN/WAN VLAN IDs, IP ranges,
  and DHCP ranges (with validation and re-prompting on bad input).
- **2 NICs** → dedicated WAN (plain physical, DHCP) and LAN, each on its own
  port. On a TTY it asks **"Separate management onto its own VLAN on the LAN
  port? (requires an 802.1Q-capable switch on the LAN side) [y/N]"**:
  - **No (default):** flat, untagged LAN; management shares the LAN network.
    The web UI is served on the LAN (and over Tailscale); SSH is *not* opened
    on the LAN by default — reach the shell over Tailscale or add an inbound
    `tcp/22` firewall rule.
  - **Yes:** LAN stays untagged (a device plugged straight in still works),
    and management rides its own **tagged VLAN** on that same port. **SSH is
    then restricted to the management VLAN** (off the plain LAN), reachable
    there or over Tailscale. The web UI stays reachable on the LAN as well —
    locking the web UI down to the management segment too is planned (a
    per-interface web-UI toggle, like the ICMP toggle; see the open issues).
- **3+ NICs** → WAN, LAN, and management each get assigned to their own
  physical port (you pick which). **SSH is firewalled onto the dedicated
  management port only**; the web UI is served there and on the LAN. Any NIC
  beyond the third is left unconfigured — a
  closing note tells you it can be added as an additional LAN network later
  from the web UI. No bonding or bridging is attempted.
  If you'd rather keep the single-trunk VLAN layout anyway on any multi-NIC
  box (e.g. to match other spud-router installs), answer yes to "Configure
  as a VLAN trunk on a single NIC instead?" when prompted.
- **Non-interactive multi-NIC installs** (no TTY, e.g. scripted/CI
  provisioning) skip all prompts and use env var overrides:
  ```bash
  SPUD_WAN_IF=eth0 SPUD_LAN_IF=eth1 SPUD_MGMT_MODE=vlan SPUD_MGMT_VLAN_ID=99 sudo -E bash install.sh
  ```
  - `SPUD_WAN_IF` / `SPUD_LAN_IF` — pick WAN/LAN explicitly (default:
    auto-selects the first two detected NICs, logged in the install log).
  - `SPUD_MGMT_MODE=lan|vlan|nic` — `lan` folds management into LAN (2-NIC
    non-interactive default), `vlan` puts it on a tagged VLAN on the LAN
    port (needs `SPUD_MGMT_VLAN_ID`, default `99`), `nic` dedicates a third
    physical port (3-NIC non-interactive default; needs `SPUD_MGMT_IF`, or
    it auto-picks the next free NIC).
  - Requesting `SPUD_MGMT_MODE=nic` with no free NIC available (e.g. only 2
    NICs total) falls back to `lan` rather than failing the install.
  - `SPUD_MGMT_ADDR_MODE=dhcp|static` — how the management interface itself
    gets addressed, whenever it has a dedicated home (`vlan` or `nic` mode;
    irrelevant for folded `lan` mode). Default is `dhcp`: join an existing
    management network and take a (reservation-pinned) lease from its own
    DHCP server, rather than spud-router owning yet another subnet. `static`
    restores spud-router's own mgmt IP + DHCP server, the original behavior.
    See [Management interface addressing](#management-interface-addressing)
    below.

### 4. Connect

Steps 4–6 assume the single-NIC default topology below. On a multi-NIC
install, connect to whichever interface/IP the installer printed in its
closing summary instead (the management interface if you assigned one, or
the LAN interface's IP if management was folded into LAN).

Plug a laptop into the Le Potato's LAN port (untagged):

- Laptop gets `192.168.1.100–150` via DHCP
- Open **https://192.168.1.1:8080** (self-signed TLS cert — accept the browser warning)
- Sign in with credentials set during install

### 5. Apply

The router ships with a sensible default layout. Click **⚡ Apply** to activate it:

| Network | Interface | IP | DHCP |
|---------|-----------|----|------|
| Management (untagged) | `eth0` | `192.168.1.1/24` | `192.168.1.100-150` |
| WAN (VLAN 2) | `eth0.2` | DHCP from ISP | — |
| LAN (VLAN 10) | `eth0.10` | `192.168.10.1/24` | `192.168.10.100-200` |

Then plug the Le Potato into a managed switch trunk port. Configure the switch so VLAN 2 connects to your modem (WAN), and VLAN 10 is your LAN.

You can add more VLANs, firewall rules, DNS entries, and routes from the web UI — no SSH needed.

### 6. SSH CLI access

```bash
ssh spud@192.168.1.1
```

Logs you straight into the interactive TUI — same features as the web UI. The `spud` user's shell is `spud-cli`, so the menu launches automatically on login.

> **Note:** SSH is only permitted on the management interface and over any enabled VPN provider by default — not on LAN VLANs. To allow SSH from a LAN VLAN, add an inbound `tcp/22` rule for that VLAN in the web UI's Firewall tab.

---

## Managed Switch Setup

| Switch port | Mode | VLANs |
|-------------|------|--------|
| Port 1 → Le Potato | Trunk | All VLANs tagged (2 = WAN, 10 = LAN, etc.) |
| Port 2 → Modem/ONT | Access | VLAN 2 untagged (WAN) |
| Ports 3–4 (LAN) | Access | VLAN 10 untagged |
| Ports 5+ | Configure as needed via web UI |

---

## Repo Structure

```
spud-router/
├── backend/
│   ├── main.py               # FastAPI backend entrypoint
│   ├── api_keys.py           # Scoped API key create/list/revoke/validate helpers
│   ├── auth.py               # Stateless HMAC session auth
│   ├── state.py              # State persistence (state.json)
│   ├── staging.py            # Transactional staging pipeline core
│   ├── models.py             # Pydantic models
│   ├── apply_core.py         # Config generation + activation
│   ├── priv.py               # Privilege helper (conditional sudo)
│   ├── vpn_coexistence.py    # Blocks >1 VPN provider claiming the default route
│   ├── tailscale_apply.py    # Tailscale config logic
│   ├── wireguard_apply.py    # WireGuard config logic
│   ├── nebula_apply.py       # Nebula config logic
│   ├── update.py             # OTA update engine
│   ├── run-update.sh         # Detached update/revert/TLS-restart wrapper
│   ├── spud-cli              # Interactive shell TUI
│   ├── ssh-banner            # ASCII banner before SSH prompt
│   ├── motd                  # Dynamic MOTD (status after login)
│   ├── routers/              # FastAPI route handlers
│   │   ├── api_keys.py
│   │   ├── auth.py
│   │   ├── bgp.py
│   │   ├── config.py
│   │   ├── diagnostics.py
│   │   ├── firewall.py
│   │   ├── mcp_mgmt.py       # API key/config helpers for AI agent setup
│   │   ├── nebula.py
│   │   ├── network.py        #   VLANs, DHCP reservations, routes, DNS
│   │   ├── snmp.py
│   │   ├── staging.py        #   MCP/programmatic transactional endpoints
│   │   ├── syslog.py
│   │   ├── system.py         #   Health, reboot, TLS cert, system monitor
│   │   ├── tailscale.py
│   │   ├── update.py
│   │   ├── wireguard.py
│   │   └── wireless.py
│   ├── cli/                  # spud-cli package (stdlib only)
│   │   ├── main.py
│   │   ├── api.py
│   │   ├── ui.py
│   │   └── tabs/             #   One module per CLI screen (vpn.py splits into tailscale/wireguard/nebula)
│   ├── mcp/                  # stdio MCP server for AI clients
│   │   ├── __main__.py       #   spud-router-mcp entry point
│   │   ├── config.py
│   │   ├── http_client.py
│   │   ├── server.py
│   │   └── tools.py
│   ├── generators/           # Config file generators
│   │   ├── netplan.py
│   │   ├── dnsmasq.py
│   │   ├── iptables.py
│   │   ├── hostapd.py
│   │   ├── syslog.py
│   │   ├── snmp.py
│   │   ├── doh.py
│   │   ├── bgp.py
│   │   ├── wireguard.py
│   │   └── nebula.py
│   └── tests/                # pytest suite
├── frontend/                 # React SPA (Vite)
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── main.jsx
│       ├── App.jsx           # Tab routing + global UI
│       ├── api.js            # Fetch wrapper with cookie auth
│       ├── components/       # Shared UI components (incl. ProviderSection for VPN)
│       └── tabs/             # One module per tab
├── deploy/                   # Install-time assets
│   ├── sudoers               # Granular sudo grants
│   ├── packages              # Apt dependency manifest
│   ├── spud-commit.sh        # Apply confirm/rollback helper
│   ├── dnsproxy-doh.service
│   ├── nebula.service
│   └── spud-router-mcp.service
├── docs/
│   └── images/               # Screenshots (see collapsible gallery above)
├── install.sh
├── VERSION                   # Installed/released version
├── .gitignore
└── README.md
```

**Release tarball contents** (built by CI, not committed):
```
spud-router-v1.0.0.tar.gz
├── install.sh
├── backend/            (FastAPI app, generators, CLI)
├── deploy/             (sudoers, packages, spud-commit.sh, *.service units)
├── spud-cli
├── ssh-banner
├── motd
├── update.py
├── run-update.sh
├── index.html          (built frontend)
├── assets/             (Vite JS/CSS chunks)
└── VERSION
```

---

## Releasing a New Version

```bash
git tag v1.1.0
git push origin v1.1.0
```

GitHub Actions will:
1. Build the frontend (`npm ci && npm run build`)
2. Package `install.sh` + `backend/` + built `dist/` into `spud-router-v1.1.0.tar.gz`
3. Create a GitHub Release with the tarball attached

---

## Development

### Backend

```bash
# from the repo root — the app is the `backend` package
python3 -m venv backend/venv && source backend/venv/bin/activate
pip install fastapi "uvicorn[standard]"
uvicorn backend.main:app --reload --port 8080
```

### Frontend

```bash
cd frontend
npm install
npm run dev       # Dev server on :3000, proxies /api → localhost:8080
```

The Vite dev server proxies all `/api` requests to the backend — no mock data or special config needed. Just run both and open `http://localhost:3000`.

### Deploying an update to an existing install

```bash
# Backend only — no rebuild needed
scp -r backend/* root@<potato-ip>:/opt/spud-router/backend/
ssh root@<potato-ip> systemctl restart spud-router

# Frontend only — build first, then copy
cd frontend && npm run build
scp ../dist/index.html root@<potato-ip>:/opt/spud-router/static/index.html
scp -r ../dist/assets/ root@<potato-ip>:/opt/spud-router/static/
# No restart needed
```

### Build with Nix (experimental / not yet tested)

A `flake.nix` at the repo root packages the backend + built frontend and
provides a pinned dev shell, as an alternative to the manual venv/npm steps
above. It is a **starting-point draft that has never been built or evaluated**
(no Nix environment was available when it was written). What that means for you
right now:

- **`nix develop` should work** — the dev shell just pulls Python 3.12 + Node 22
  from nixpkgs, so you can enter it and run the tests / dev servers today.
- **`nix build` / `nix run` do not work yet** — the frontend derivation's
  `npmDepsHash` is a placeholder (`pkgs.lib.fakeHash`), so the build will fail
  with a hash mismatch until someone fills in the real hash in an actual Nix
  environment. The `../dist` capture and the `uvicorn[standard]` extras list
  are also unverified until that first real build (see the header comment in
  `flake.nix` for the full TODO list).

It does **not** replace `install.sh` for real device deployment; that stays the
supported path.

```bash
# enter a dev shell with the pinned Python 3.12 + Node 22 toolchain (works today)
nix develop

# inside the shell: run the backend / tests / frontend as usual
cd backend && python -m pytest tests/ -q
uvicorn backend.main:app --reload --port 8080

# build/run the packaged app — NOT working yet: needs a real npmDepsHash
# computed in a Nix environment first (fails with a hash mismatch until then)
nix build   # backend + built frontend staged into static/
nix run     # run the built package directly
```

---

## Service Management

```bash
systemctl status spud-router
journalctl -u spud-router -f
systemctl restart spud-router

# Config files written by Apply:
/etc/netplan/50-spud-router.yaml
/etc/dnsmasq.d/spud-router.conf
/etc/spud-router/iptables.sh
/etc/hostapd/hostapd.conf               # only when wireless is enabled
/etc/rsyslog.d/60-spud-router-remote.conf
/etc/snmp/snmpd.conf
/etc/dnsproxy-doh.yaml                  # only when DoH mode is enabled
/etc/frr/frr.conf                       # 0640 frr:frr — only when BGP is enabled
/etc/wireguard/wg0.conf                 # 0600, holds the private key — only when WireGuard is enabled
/etc/nebula/{config.yaml,ca.crt,host.crt,host.key}   # host.key is 0600 — only when Nebula is enabled

# Persisted kernel settings:
/etc/sysctl.d/99-spud-router.conf       # IP forwarding
/etc/iptables/rules.v4                  # iptables restored on boot

# State and credentials:
/etc/spud-router/state.json
/etc/spud-router/auth.json              # chmod 600
/etc/spud-router/token-secret           # HMAC signing key, chmod 600
```

---

## Troubleshooting

**Can't reach web UI after install**
- Check: `ip addr show eth0` — should have `192.168.1.1/24`
- Check: `systemctl status spud-router`
- Logs: `journalctl -u spud-router -n 50`

**dnsmasq won't start**
- Port 53 conflict: `ss -tulnp | grep :53`
- If `systemd-resolved` is holding port 53, its stub listener should be off: confirm `DNSStubListener=no` in `/etc/systemd/resolved.conf.d/spud-router.conf`, then `systemctl restart systemd-resolved`

**netplan apply fails**
- Debug: `netplan generate --debug`
- Config: `/etc/netplan/50-spud-router.yaml`

**VLANs not working**
- Check module: `lsmod | grep 8021q` — if missing: `modprobe 8021q`
- Check interfaces: `ip -br link | grep eth0`

**Tailscale won't authenticate**
- Run `tailscale up` manually once (requires browser for first auth), or set an auth key in the web UI or CLI

**WireGuard peer can't connect**
- Server mode: check the listen port is reachable on WAN (`systemctl status wg-quick@wg0`, `ss -ulnp | grep <port>`)
- Client mode dials out, so it needs no inbound WAN rule — check the peer's `endpoint` is reachable instead
- Re-download the peer's `.conf`/QR if the private key was regenerated — it's only ever shown once and never re-derivable

**Nebula host won't join the mesh**
- Check: `systemctl status nebula`
- Re-import cert/key/CA from the VPN tab if any of the three don't match — the router validates the triple (`nebula-cert verify` + expiry + a live smoke test) before saving, so a rejected import means one of them is wrong, not a bug
- Confirm the lighthouse hosts/static host map point at a reachable address

**BGP session won't establish**
- Check: `systemctl status frr` and `vtysh -c "show ip bgp summary"`
- A neighbor reachable over a LAN/mgmt VLAN needs an inbound `tcp/179` allow rule from that peer's IP added in the Firewall tab — BGP does not auto-open this port (unlike SNMP/WireGuard's listen ports)
- Confirm the local ASN, router-id, and the neighbor's remote-AS all match what the peer expects

**Outbound (egress) firewall is blocking traffic I want**
- Check the default outbound policy in the Firewall tab — if set to "deny", add explicit allow rules for the traffic you need
- Outbound rules are evaluated in list order; first match wins
- Management interface egress to WAN is always allowed (can't lock out admin access)

**Wireless AP won't start**
- Check: `iw dev` to confirm your wireless interface supports AP mode
- Check: `systemctl status hostapd`
- Verify the country code is set correctly (`iw reg set <CC>`)
- Some USB adapters require `nl80211` — check `iw phy` output

**dnsproxy / DoH not working**
- Check: `systemctl status dnsproxy-doh`
- Logs: `journalctl -u dnsproxy-doh -n 30`
- Confirm DNS mode is set to "DoH" in the WAN tab and dnsproxy logs show `entering listener loop` for both `udp` and `tcp`
- The router will fall back to direct DNS if dnsproxy is unhealthy (built-in fail-safe)

**SNMP not responding**
- Check: `systemctl status snmpd`
- Verify the allowlist includes your monitoring host's IP
- If you changed the bind interface, confirm the interface is up
- Test locally: `snmpwalk -v2c -c <community> 127.0.0.1`

**Remote syslog not forwarding**
- Check: `systemctl status rsyslog`
- Use the "Test Connection" button in the web UI's Logging tab (UDP: sends a test message; TCP/TLS: attempts a socket connection)
- Verify the remote server is reachable and listening on the configured port/protocol

**OTA update failed**
- Check: `journalctl -u spud-router-update -n 50` (the transient update unit)
- The update engine automatically rolls back on failure — confirm the previous version is running via `GET /api/health`
- Manual fallback: SSH in and run `sudo python3 /opt/spud-router/update.py --apply`
- To revert manually: `sudo python3 /opt/spud-router/update.py --revert`

**Can't SSH from a device on a LAN VLAN**
- This is the default, not a bug: SSH is only permitted on the management interface and over any enabled VPN provider. Add an inbound `tcp/22` rule for that VLAN in the web UI's Firewall tab to allow it.

**TLS certificate warning in browser**
- The default install generates a self-signed cert — this is expected
- To upload a trusted cert (e.g. from Let's Encrypt), use the TLS Certificate section in the Settings tab
- Or regenerate a new self-signed cert from the same panel

---

## License

[GNU Affero General Public License v3.0 (AGPL-3.0)](https://www.gnu.org/licenses/agpl-3.0.html) — see [LICENSE](LICENSE).

spud_router is free software: anyone may use, study, modify, and share it. Any derivative — including a modified version offered to others over a network — must be released under the same license with its source available, so the project stays free forever.
