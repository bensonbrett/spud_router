# Changelog

All notable changes to spud-router are recorded here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
follows semantic versioning (`vMAJOR.MINOR.PATCH`).

Each released version's section below is published as the **"What's new"**
summary at the top of its [GitHub Release](https://github.com/bensonbrett/spud_router/releases),
followed by the auto-generated "What's Changed" pull-request list. When cutting a
release, move the relevant `Unreleased` notes into a new version section.

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
