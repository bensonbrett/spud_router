# Changelog

All notable changes to spud-router are recorded here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows semantic versioning (`vMAJOR.MINOR.PATCH`).

Each released version's section below is published as the **"What's new"**
summary at the top of its [GitHub Release](https://github.com/bensonbrett/spud_router/releases),
followed by the auto-generated "What's Changed" pull-request list. When cutting a
release, move the relevant `Unreleased` notes into a new version section.

## Project status

spud-router is a router-on-a-stick management app — a FastAPI backend, a React
single-page web UI, and a stdlib-only text UI (`spud-cli`, over SSH) that all
drive the same JSON state, plus a Model Context Protocol (MCP) server so AI
agents can manage the router too. These four surfaces are held at deliberate
feature parity. From that state it generates netplan, dnsmasq, iptables,
hostapd, rsyslog, and SNMP configuration.

As of **v0.11.0**, the platform provides: VLANs and tiered single-/multi-NIC
topologies; WAN (DHCP or static) with DNS-over-HTTPS upstream; a full firewall
(inbound, inter-VLAN, and outbound rules, port forwarding, per-interface web-UI
and ping toggles with an anti-lockout guard); static routes, local DNS, DHCP
reservations, and Wake-on-LAN; IPv4 BGP dynamic routing via FRR; three VPN
providers — Tailscale, WireGuard, and Nebula (join-only overlay mesh); SNMP and
remote syslog; a management interface with DHCP-client or static addressing; and
OTA updates with commit-confirmed apply, a connectivity watchdog that
auto-reverts a bad change, and backup/rollback. Wireless (hostapd AP), the VPN
providers, and BGP are implemented but still carry "needs hardware verification"
caveats where a live peer or radio is required.

Release notes below begin with v0.11.0 (the first release with curated notes);
earlier entries are grouped by minor version and reconstructed from merged pull
requests. The per-release GitHub pages for those older tags show only the
install snippet — this file is the canonical history.

## [Unreleased]

## [0.11.0] - 2026-07-15
Backup/restore no longer loses your VPN configuration, and both the MCP server
and WireGuard peers can now be edited in place instead of deleting and re-adding.

### Fixed
- Config backup/restore no longer silently drops BGP, WireGuard, and Nebula
  configuration on import — a full round-trip now preserves every domain (#234).

### Added
- Edit an enabled MCP server's connection/safety settings (`base_url`,
  `tls_verify`, `read_only`, confirm-window) in place, without rotating the API
  key — from the Web UI, TUI, or MCP (#236).
- Edit a WireGuard peer's name, allowed IPs, endpoint, and keepalive in place
  (the public key stays fixed) — from the Web UI, TUI, and MCP (#235).

## [0.10.x] - 2026-07-14
Management-interface addressing became flexible, and a broad four-surface
parity sweep brought the TUI and MCP up to the web UI's feature set.

### Added
- Management interface **DHCP-client addressing**, plus an option to run (or not
  run) a DHCP server on the management interface; matching `dhcp6-overrides`
  emitted so `netplan apply` doesn't fail on dual-stack (#213).
- Per-interface **web-UI (port 8080) toggle** with an all-off lockout guard that
  refuses any config leaving the web UI unreachable on every interface.
- **MCP tool coverage** for BGP, DHCP reservations, syslog, SNMP, port
  forwarding, and SSIDs; TUI management-addressing parity with the web UI
  (#230, #231, #232).

### Fixed
- SNMP/syslog: resolve `bind_interface` to an IP at apply time; load the syslog
  drop-in before the default rules so `keep_local=false` works.
- BGP: clear a stale `frr.conf` when BGP is disabled.
- Docs/screenshots refreshed; SNMP and remote syslog marked hardware-verified.

## [0.9.x] - 2026-07-14
The installer learned to shape itself to the hardware it lands on.

### Added
- Installer **tiers the network topology by physical NIC count** — single-NIC vs
  multi-NIC installs branch automatically, with NIC auto-detection and an honest
  SSH-access summary.
- Ubuntu 26.04 compatibility (package names, sudoers `requiretty`).
- A starting-point `flake.nix` for Nix packaging (unbuilt/untested).

### Fixed
- `/api/interfaces`: include VLAN subinterfaces and strip the `@parent` suffix so
  interface names match stored state (#188).

## [0.8.x] - 2026-07-08
Hardening pass on OTA, the netns test harness, and BGP service lifecycle.

### Fixed
- OTA now flags when an update changes generated config but hasn't been applied.
- Eliminated the veth-rename race in the privileged netns behavioral tests (poll
  for visibility before renaming; avoid collisions with real host interfaces).
- BGP: leave FRR disabled until BGP is enabled; use `restart`, not `reload`.
- Web UI surfaces an error whenever a Save/Delete action fails.

## [0.7.x] - 2026-07-07
Dynamic routing arrives, and the MCP integration matures.

### Added
- **IPv4 BGP dynamic routing** via FRR `bgpd`.
- MCP polish: `inputSchema` on every tool, correct stdio behaviour (ignore
  JSON-RPC notifications), a `spud-router-mcp` local-client CLI entry point, a
  `--config` flag, and an AI-Agent setup UI refactor.

### Fixed
- Validate `mgmt_ip` and the mgmt DHCP range as IPv4; match the ping toggle by
  destination IP rather than input interface.

## [0.6.x] - 2026-07-06
The MCP server and API-key auth land — the AI-management surface is born.

### Added
- **API-key authentication** and an **MCP server** with a transactional staging
  pipeline (`begin → op → validate → commit → confirm`).

### Changed
- DoH: replaced the removed `cloudflared proxy-dns` with **AdGuard dnsproxy**,
  run under `DynamicUser`.
- Auth moved to HTTP-only cookies (token no longer returned in the login
  response body); TLS certificate SAN now covers all device IPs.

## [0.5.x] - 2026-07-04
### Added
- **Wake-on-LAN**.

### Fixed
- Diagnostics reliability (ping/traceroute/nslookup); early version-numbering
  cleanup.

## [0.4.x] - 2026-07-04
The foundational release — the first tag, atop roughly 150 commits of initial
development. Everything the rest of the project builds on.

### Added
- Core platform: FastAPI backend, React SPA, and a stdlib-only TUI (`spud-cli`
  over SSH) driving a shared JSON state, with netplan/dnsmasq/iptables config
  generators.
- **VLANs**, **WAN** (DHCP or static), a full **firewall** (inbound, inter-VLAN,
  and outbound rules) with ICMP support, **static routes**, and local **DNS**
  entries.
- Three **VPN providers** — Tailscale, WireGuard, and Nebula (join-only overlay
  mesh) — behind a shared multi-provider VPN tab.
- **DNS-over-HTTPS** upstream with an optional block of outbound port 53.
- **SNMP** (Net-SNMP v2c) and **remote syslog** forwarding.
- **Diagnostics** (ping/traceroute/nslookup).
- **OTA updates** with commit-confirmed apply, a connectivity-watchdog
  auto-revert, backup/rollback, and remote reboot.
- TLS certificate upload/regenerate; SPDX license headers across first-party
  source.
