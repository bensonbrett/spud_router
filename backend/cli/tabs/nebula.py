# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Nebula configuration and credential-import tab (join-only, see #91)."""
from ..api import DELETE, GET, POST, PUT
from ..ui import (
    dim, err, hi, ok, warn,
    clear, confirm, menu, multiline_prompt, pause, print_logo,
    print_status_bar, prompt, section, table,
)


def screen(state: dict) -> None:
    while True:
        clear()
        print_logo()
        print_status_bar(state)
        section("Nebula")

        try:
            cfg = GET("/api/nebula")
        except RuntimeError as e:
            print(err(f"\n  Error loading config: {e}"))
            pause()
            return

        cert_info = cfg.get("cert_info")
        ca_info   = cfg.get("ca_info")

        table(["Setting", "Value"], [
            ["Enabled",          ok("yes") if cfg.get("enabled") else dim("no")],
            ["Listen port",      hi(str(cfg.get("listen_port", 4242)))],
            ["Lighthouse hosts", ", ".join(cfg.get("lighthouse_hosts", [])) or dim("none")],
            ["Host cert",        _cert_summary(cert_info)],
            ["CA cert",          _cert_summary(ca_info)],
            ["Firewall (in/out)", f"{len(cfg.get('firewall_inbound', []))} / {len(cfg.get('firewall_outbound', []))} rules"],
        ])

        idx = menu("Nebula Actions", [
            ("Toggle enable/disable", ""),
            ("Set listen port", ""),
            ("Edit lighthouse hosts", ""),
            ("Edit static host map", ""),
            ("Import credentials", warn("multi-line PEM paste")),
            ("Clear credentials", ""),
            ("Reload", ""),
        ])
        if idx == -1:
            return
        if idx == 0:
            _toggle(cfg)
        elif idx == 1:
            _set_listen_port(cfg)
        elif idx == 2:
            _edit_lighthouse_hosts(cfg)
        elif idx == 3:
            _edit_static_host_map(cfg)
        elif idx == 4:
            _import_credentials()
        elif idx == 5:
            _clear_credentials()
        state = GET("/api/state")


def _cert_summary(info: dict | None) -> str:
    if not info:
        return dim("not imported")
    status = err("expired") if info.get("expired") else ok("valid")
    return f"{info.get('name') or '(unnamed)'} — {status}, expires {info.get('not_after', '?')}"


def _save(cfg: dict, **changes) -> None:
    body = {
        "enabled": cfg.get("enabled", False),
        "listen_port": cfg.get("listen_port", 4242),
        "lighthouse_hosts": cfg.get("lighthouse_hosts", []),
        "static_host_map": cfg.get("static_host_map", {}),
        "firewall_inbound": cfg.get("firewall_inbound", []),
        "firewall_outbound": cfg.get("firewall_outbound", []),
        **changes,
    }
    try:
        PUT("/api/nebula", body)
        print(ok("\n  ✓ Saved"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _toggle(cfg: dict) -> None:
    _save(cfg, enabled=not cfg.get("enabled", False))


def _set_listen_port(cfg: dict) -> None:
    val = prompt("Listen port", str(cfg.get("listen_port", 4242)))
    try:
        port = int(val)
    except ValueError:
        print(err("\n  Invalid port"))
        pause()
        return
    _save(cfg, listen_port=port)


def _edit_lighthouse_hosts(cfg: dict) -> None:
    section("Lighthouse Hosts")
    hosts = list(cfg.get("lighthouse_hosts", []))
    while True:
        print()
        if hosts:
            for i, h in enumerate(hosts, 1):
                print(f"  {i}. {hi(h)}")
        else:
            print(dim("  No lighthouses configured"))
        print(dim("\n  Enter an overlay IP to add, a number to remove, or Enter to save"))
        try:
            val = prompt("").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not val:
            break
        try:
            i = int(val) - 1
            if 0 <= i < len(hosts):
                print(dim(f"  Removed {hosts.pop(i)}"))
            else:
                print(err("  Invalid number"))
        except ValueError:
            if val not in hosts:
                hosts.append(val)
                print(ok(f"  Added {val}"))
    _save(cfg, lighthouse_hosts=hosts)


def _edit_static_host_map(cfg: dict) -> None:
    section("Static Host Map")
    host_map = {k: list(v) for k, v in cfg.get("static_host_map", {}).items()}
    while True:
        print()
        rows = [(ip, ep) for ip, eps in host_map.items() for ep in eps]
        if rows:
            for i, (ip, ep) in enumerate(rows, 1):
                print(f"  {i}. {hi(ip)} → {ep}")
        else:
            print(dim("  No entries"))
        print(dim("\n  Enter a number to remove, 'a' to add, or Enter to save"))
        try:
            val = prompt("").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not val:
            break
        if val.lower() == "a":
            ip = prompt("Lighthouse overlay IP", "")
            endpoint = prompt("Public endpoint (host:port)", "")
            if ip and endpoint:
                host_map.setdefault(ip, []).append(endpoint)
                print(ok(f"  Added {ip} → {endpoint}"))
            continue
        try:
            i = int(val) - 1
            if 0 <= i < len(rows):
                ip, ep = rows[i]
                host_map[ip].remove(ep)
                if not host_map[ip]:
                    del host_map[ip]
                print(dim(f"  Removed {ip} → {ep}"))
            else:
                print(err("  Invalid number"))
        except ValueError:
            print(err("  Enter a number, 'a', or Enter"))
    _save(cfg, static_host_map=host_map)


def _import_credentials() -> None:
    section("Import Nebula Credentials")
    print(dim("  spud-router only joins an existing mesh — it never signs certs."))
    print(dim("  Generate a host cert/key and CA off-device with nebula-cert.\n"))

    cert_pem = multiline_prompt("Host certificate (PEM):")
    if not cert_pem:
        print(err("\n  No certificate entered"))
        pause()
        return
    key_pem = multiline_prompt("Host private key (PEM):")
    if not key_pem:
        print(err("\n  No key entered"))
        pause()
        return
    ca_pem = multiline_prompt("CA certificate (PEM):")
    if not ca_pem:
        print(err("\n  No CA certificate entered"))
        pause()
        return

    try:
        resp = POST("/api/nebula/credentials", {"cert_pem": cert_pem, "key_pem": key_pem, "ca_pem": ca_pem})
        info = resp.get("cert_info") or {}
        print(ok(f"\n  ✓ Credentials imported — {info.get('name', '(unnamed)')}, expires {info.get('not_after', '?')}"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _clear_credentials() -> None:
    if not confirm("Clear stored Nebula credentials? This will stop Nebula until new ones are imported"):
        return
    try:
        DELETE("/api/nebula/credentials")
        print(ok("\n  ✓ Credentials cleared"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()
