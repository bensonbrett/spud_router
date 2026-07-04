# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""WireGuard configuration and peer management tab."""
import subprocess

from ..api import DELETE, GET, POST, PUT
from ..ui import (
    dim, err, hi, ok, warn,
    clear, confirm, menu, pause, print_logo,
    print_status_bar, prompt, section, table,
)


def screen(state: dict) -> None:
    while True:
        clear()
        print_logo()
        print_status_bar(state)
        section("WireGuard")

        try:
            cfg = GET("/api/wireguard")
        except RuntimeError as e:
            print(err(f"\n  Error loading config: {e}"))
            pause()
            return

        peers = cfg.get("peers", [])

        table(["Setting", "Value"], [
            ["Enabled",     ok("yes") if cfg.get("enabled") else dim("no")],
            ["Mode",        hi(cfg.get("mode", "server"))],
            ["Listen port", hi(str(cfg.get("listen_port", 51820)))],
            ["Address",     hi(cfg.get("address") or dim("not set"))],
            ["Key",         ok("set") if cfg.get("has_key") else dim("not set")],
            ["Public key",  dim(cfg.get("public_key") or "—")],
            ["Peers",       hi(str(len(peers)))],
        ])
        print()

        if peers:
            table(["Name", "Public key", "Allowed IPs", "Endpoint"], [
                [p.get("name") or "—", p["public_key"][:16] + "…",
                 ", ".join(p.get("allowed_ips", [])) or "—", p.get("endpoint") or "—"]
                for p in peers
            ])

        idx = menu("WireGuard Actions", [
            ("Toggle enable/disable", ""),
            ("Set mode (server/client)", ""),
            ("Set listen port", ""),
            ("Set tunnel address", ""),
            ("Regenerate key",  warn("replaces this device's identity")),
            ("Add peer",        ""),
            ("Remove peer",     ""),
            ("Reload",          ""),
        ])
        if idx == -1:
            return
        if idx == 0:
            _toggle(cfg, "enabled")
        elif idx == 1:
            _set_mode(cfg)
        elif idx == 2:
            _set_listen_port(cfg)
        elif idx == 3:
            _set_address(cfg)
        elif idx == 4:
            _regenerate_key()
        elif idx == 5:
            _add_peer(cfg)
        elif idx == 6:
            _remove_peer(peers)
        state = GET("/api/state")


def _save(cfg: dict, **changes) -> None:
    body = {
        "enabled": cfg.get("enabled", False),
        "mode": cfg.get("mode", "server"),
        "listen_port": cfg.get("listen_port", 51820),
        "address": cfg.get("address", ""),
        "private_key": cfg.get("private_key", ""),
        **changes,
    }
    try:
        PUT("/api/wireguard", body)
        print(ok("\n  ✓ Saved"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _toggle(cfg: dict, key: str) -> None:
    _save(cfg, **{key: not cfg.get(key, False)})


def _set_mode(cfg: dict) -> None:
    idx = menu("Mode", [
        ("server", "accept peer connections"),
        ("client", "dial out to a peer"),
    ])
    if idx == -1:
        return
    _save(cfg, mode=("server", "client")[idx])


def _set_listen_port(cfg: dict) -> None:
    val = prompt("Listen port", str(cfg.get("listen_port", 51820)))
    try:
        port = int(val)
    except ValueError:
        print(err("\n  Invalid port"))
        pause()
        return
    _save(cfg, listen_port=port)


def _set_address(cfg: dict) -> None:
    val = prompt("Tunnel address (e.g. 10.100.0.1/24)", cfg.get("address", ""))
    _save(cfg, address=val)


def _regenerate_key() -> None:
    if not confirm("Regenerate this device's WireGuard key? Existing peers will need the new public key"):
        return
    try:
        resp = POST("/api/wireguard/regenerate-key")
        print(ok(f"\n  ✓ New public key: {resp['public_key']}"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _add_peer(cfg: dict) -> None:
    section("Add Peer")
    name = prompt("Peer name", "")
    allowed_ips_raw = prompt("Allowed IPs (comma-separated CIDRs)", "")
    endpoint = prompt("Endpoint (host:port, blank if none)", "")

    body = {
        "name": name,
        "allowed_ips": [x.strip() for x in allowed_ips_raw.split(",") if x.strip()],
        "endpoint": endpoint or None,
    }

    if confirm("Generate a keypair for this peer (recommended for clients you manage)?"):
        body["client_address"] = prompt("Peer's tunnel address (e.g. 10.100.0.2/32)", "")
    else:
        body["public_key"] = prompt("Peer's public key", "")

    try:
        resp = POST("/api/wireguard/peers", body)
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
        pause()
        return

    print(ok("\n  ✓ Peer added"))
    if resp.get("client_config"):
        _reveal_client_config(name, resp["private_key"], resp["client_config"])
    else:
        pause()


def _reveal_client_config(name: str, private_key: str, client_config: str) -> None:
    section(f"New peer: {name or 'unnamed'}")
    print(warn("  This private key is shown once and is not stored — save it now.\n"))
    print(client_config)
    try:
        proc = subprocess.run(["qrencode", "-t", "ANSIUTF8"], input=client_config,
                               capture_output=True, text=True)
        if proc.returncode == 0:
            print(proc.stdout)
        else:
            print(dim("  (qrencode unavailable — scan not shown, use the text config above)"))
    except FileNotFoundError:
        print(dim("  (qrencode not installed — scan not shown, use the text config above)"))
    pause()


def _remove_peer(peers: list) -> None:
    if not peers:
        print(dim("\n  No peers to remove"))
        pause()
        return
    idx = menu("Remove which peer?", [
        (p.get("name") or p["public_key"][:16] + "…", ", ".join(p.get("allowed_ips", [])))
        for p in peers
    ])
    if idx == -1:
        return
    peer_id = peers[idx]["id"]
    try:
        DELETE(f"/api/wireguard/peers/{peer_id}")
        print(ok("\n  ✓ Peer removed"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()
