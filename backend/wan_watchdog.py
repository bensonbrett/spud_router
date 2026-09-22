#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Root-owned, bounded WAN self-healing watchdog (#306)."""
import json
import os
import subprocess
import time
from pathlib import Path

STATE_FILE = Path("/etc/spud-router/state.json")
STATUS_FILE = Path("/var/lib/spud-router/wan-watchdog-status.json")
PING = "/usr/bin/ping"
NETWORKCTL = "/usr/bin/networkctl"
SYSTEMCTL = "/usr/bin/systemctl"


def _read(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _write(status):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    status["updated_at"] = int(time.time())
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(status))
    tmp.replace(STATUS_FILE)
    STATUS_FILE.chmod(0o644)


def _run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=8).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _gateway():
    try:
        out = subprocess.run(["/usr/sbin/ip", "-4", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return "", ""
    parts = out.split()
    return (parts[parts.index("via") + 1] if "via" in parts else "", parts[parts.index("dev") + 1] if "dev" in parts else "")


def main():
    state = _read(STATE_FILE, {})
    cfg = state.get("wan_self_healing", {})
    old = _read(STATUS_FILE, {"history": [], "failure_started_at": 0, "last_reboot_at": 0})
    now = int(time.time())
    if not cfg.get("enabled", False):
        _write({**old, "state": "disabled", "message": "WAN self-healing is disabled."})
        return
    gateway, wan_if = _gateway()
    gateway_ok = bool(gateway) and _run(PING, "-c", "1", "-W", "3", gateway)
    probes = {host: _run(PING, "-c", "1", "-W", "3", host) for host in cfg.get("probe_hosts", [])}
    external_ok = any(probes.values())
    healthy = gateway_ok and external_ok
    history = old.get("history", [])[-19:]
    if healthy:
        if old.get("failure_started_at"):
            history.append({"at": now, "event": "recovered", "message": "Gateway and external WAN probes recovered."})
        _write({**old, "state": "healthy", "message": "Gateway and external WAN probes are reachable.", "gateway": gateway, "gateway_ok": True, "probes": probes, "failure_started_at": 0, "history": history})
        return
    started = old.get("failure_started_at") or now
    elapsed = now - started
    threshold = int(cfg.get("failure_minutes", 60)) * 60
    status = {**old, "state": "unhealthy", "message": "WAN health check failed.", "gateway": gateway, "gateway_ok": gateway_ok, "probes": probes, "failure_started_at": started, "history": history}
    if elapsed < threshold:
        status["message"] = f"WAN unhealthy for {elapsed // 60} minutes; waiting for configured threshold."
        _write(status)
        return
    if wan_if:
        _run(NETWORKCTL, "renew", wan_if)
        history.append({"at": now, "event": "wan_renew", "message": f"Renewed WAN interface {wan_if} after sustained failure."})
    cooldown = int(cfg.get("reboot_cooldown_minutes", 720)) * 60
    if cfg.get("reboot_enabled") and now - old.get("last_reboot_at", 0) >= cooldown:
        status.update({"state": "rebooting", "message": "Sustained WAN failure; rebooting within configured cooldown.", "last_reboot_at": now, "history": history + [{"at": now, "event": "reboot", "message": "Scheduled reboot after sustained WAN failure."}]})
        _write(status)
        subprocess.Popen([SYSTEMCTL, "reboot"])
        return
    status["message"] = "Sustained WAN failure; WAN renewal attempted. Reboot is disabled or cooling down."
    status["history"] = history
    _write(status)


if __name__ == "__main__":
    main()
