# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Shared config activation pipeline: generate → write → activate.

Used by:
  - backend/routers/config.py's POST /api/apply, running as the
    unprivileged spud-router service user (sudo=True — root-owned
    writes/service restarts go through the NOPASSWD grants in
    deploy/sudoers).
  - update.py's --revert path (the detached commit-confirm auto-revert;
    see deploy/spud-commit.sh), running as root under systemd-run
    (sudo=False — writes/restarts directly, exactly like update.py's own
    bare `systemctl restart spud-router` calls elsewhere in that file).

This module is deliberately dependency-free beyond the stdlib and other
dependency-free backend modules (generators/*, state, priv, tailscale_apply)
— no fastapi, no pydantic — so update.py can import it under the system
python3, which has no pip packages installed (see run-update.sh /
deploy/spud-commit.sh, which invoke update.py directly with
`/usr/bin/python3`, not the app's venv interpreter).

Raises RuntimeError (never HTTPException — this module has no FastAPI
dependency) on any failure, describing exactly which step failed. Callers
translate that into whatever error shape they need.
"""
import subprocess
from pathlib import Path
from typing import Callable

from . import nebula_apply, tailscale_apply, wireguard_apply
from .generators import (
    doh as doh_gen, dnsmasq, hostapd, iptables, netplan,
    nebula as nebula_gen, snmp as snmp_gen, syslog as syslog_gen,
    wireguard as wireguard_gen,
)
from .priv import cmd as _cmd
from .state import DNSMASQ_FILE, IPTABLES_SCRIPT, NETPLAN_FILE

HOSTAPD_CONF     = Path("/etc/hostapd/hostapd.conf")
RSYSLOG_CONF     = Path("/etc/rsyslog.d/60-spud-router-remote.conf")
SNMPD_CONF       = Path("/etc/snmp/snmpd.conf")
DNSPROXY_CONF    = Path("/etc/dnsproxy-doh.yaml")
WIREGUARD_CONF   = Path("/etc/wireguard/wg0.conf")
NEBULA_DIR       = Path("/etc/nebula")
NEBULA_CA        = NEBULA_DIR / "ca.crt"
NEBULA_CERT      = NEBULA_DIR / "host.crt"
NEBULA_KEY       = NEBULA_DIR / "host.key"
NEBULA_CONF      = NEBULA_DIR / "config.yaml"

# Every VPN provider's apply(state, sudo) is registered here and called
# independently, failure-isolated (see _apply_vpn_providers): one provider
# failing to come up must never tear down another the admin might be
# connected through right now. This list is the whole extension point —
# nothing else in activate_all() needs to change to add a provider.
VPN_PROVIDERS: list[tuple[str, Callable[..., list[str]]]] = [
    ("tailscale", tailscale_apply.apply),
    ("wireguard", wireguard_apply.apply),
    ("nebula", nebula_apply.apply),
]


def _apply_vpn_providers(state: dict, sudo: bool) -> list[str]:
    """
    Call every registered VPN provider's apply() independently. A provider
    that raises (or whose own apply() surfaces an error) is logged as a
    warning result and skipped — it must never prevent the *other*
    providers from being applied, since the admin could be relying on any
    one of them for connectivity right now.
    """
    results: list[str] = []
    for name, apply_fn in VPN_PROVIDERS:
        try:
            results += apply_fn(state, sudo=sudo)
        except Exception as e:
            results.append(f"⚠ {name} apply failed (other VPN providers unaffected): {e}")
    return results


def dnsproxy_healthy() -> bool:
    """
    Best-effort health check after (re)starting dnsproxy-doh: confirms the
    service is actually running, not just that `systemctl restart` returned
    without error (Restart=on-failure means it can still crash-loop right
    after a successful restart if the upstream is unreachable). Used to
    gate the outbound :53 block — see activate_all()'s fail-safe.
    """
    # is-active is a read-only bus query — no sudo/privilege needed on a
    # standard systemd install, so this doesn't need a sudoers entry.
    proc = subprocess.run(
        ["systemctl", "is-active", "dnsproxy-doh"],
        capture_output=True, text=True,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "active"


def generate_all(state: dict) -> dict:
    """Return every generated config file, keyed by name, without writing
    anything. Used by preview/dry-run callers."""
    return {
        "netplan":     netplan.generate(state),
        "dnsmasq":     dnsmasq.generate(state),
        "iptables":    iptables.generate(state),
        "hostapd":     hostapd.generate(state),
        "syslog":      syslog_gen.generate(state),
        "snmp":        snmp_gen.generate(state),
        "doh":         doh_gen.generate(state),
        "wireguard":   wireguard_gen.generate(state),
        "nebula":      nebula_gen.generate(state),
    }


def activate_all(state: dict, sudo: bool = True) -> list[str]:
    """
    Generate every config file, write it, and activate it (netplan apply,
    service restarts, tailscale up/down). Returns the list of step-result
    strings on success; raises RuntimeError with a clear message on the
    first hard failure. An unhealthy DoH proxy is NOT a hard failure — see
    the fail-safe below, it just skips the outbound :53 block.
    """
    np    = netplan.generate(state)
    dm    = dnsmasq.generate(state)
    ipt   = iptables.generate(state)
    hap   = hostapd.generate(state)
    rsys  = syslog_gen.generate(state)
    snmpc = snmp_gen.generate(state)
    doh_conf = doh_gen.generate(state)
    wg    = wireguard_gen.generate(state)
    nebula_conf = nebula_gen.generate(state)

    results: list[str] = []
    try:
        subprocess.run(
            _cmd(sudo, "tee", str(NETPLAN_FILE)),
            input=np, text=True, check=True, capture_output=True,
        )
        results.append(f"Written {NETPLAN_FILE}")

        subprocess.run(
            _cmd(sudo, "tee", str(DNSMASQ_FILE)),
            input=dm, text=True, check=True, capture_output=True,
        )
        results.append(f"Written {DNSMASQ_FILE}")

        if hap:
            subprocess.run(
                _cmd(sudo, "tee", str(HOSTAPD_CONF)),
                input=hap, text=True, check=True, capture_output=True,
            )
            results.append(f"Written {HOSTAPD_CONF}")

        # Always write the rsyslog drop-in — an empty/commented file when
        # disabled, so toggling forwarding off actually stops it rather than
        # leaving a stale forwarding rule in place.
        rsyslog_content = rsys or "# Generated by spud-router — remote syslog forwarding disabled\n"
        subprocess.run(
            _cmd(sudo, "tee", str(RSYSLOG_CONF)),
            input=rsyslog_content, text=True, check=True, capture_output=True,
        )
        results.append(f"Written {RSYSLOG_CONF}")

        # snmpd.conf only written when enabled — the service is stopped+
        # disabled below when it isn't, so a stale file left in place is inert.
        if snmpc:
            subprocess.run(
                _cmd(sudo, "tee", str(SNMPD_CONF)),
                input=snmpc, text=True, check=True, capture_output=True,
            )
            results.append(f"Written {SNMPD_CONF}")

        # wg0.conf contains the interface's private key in cleartext — tee
        # writes it as root but doesn't control the resulting mode, so an
        # explicit chmod follows immediately. Only written when enabled;
        # wireguard_apply.apply() (called via VPN_PROVIDERS, below) disables
        # the unit when it isn't, so a stale file left in place is inert.
        if wg:
            subprocess.run(
                _cmd(sudo, "tee", str(WIREGUARD_CONF)),
                input=wg, text=True, check=True, capture_output=True,
            )
            subprocess.run(_cmd(sudo, "chmod", "600", str(WIREGUARD_CONF)), check=True, capture_output=True, text=True)
            results.append(f"Written {WIREGUARD_CONF}")

        # Nebula's cert/CA are public (not sensitive) but the host private
        # key is written with the same tee-then-chmod-600 pattern as
        # WireGuard's. Only written when enabled with a complete
        # cert/key/CA triple; nebula_apply.apply() (via VPN_PROVIDERS,
        # below) disables the unit otherwise, so stale files are inert.
        if nebula_conf:
            nb = state.get("nebula", {})
            subprocess.run(
                _cmd(sudo, "tee", str(NEBULA_CA)),
                input=nb.get("ca_pem", ""), text=True, check=True, capture_output=True,
            )
            subprocess.run(
                _cmd(sudo, "tee", str(NEBULA_CERT)),
                input=nb.get("cert_pem", ""), text=True, check=True, capture_output=True,
            )
            subprocess.run(
                _cmd(sudo, "tee", str(NEBULA_KEY)),
                input=nb.get("key_pem", ""), text=True, check=True, capture_output=True,
            )
            subprocess.run(_cmd(sudo, "chmod", "600", str(NEBULA_KEY)), check=True, capture_output=True, text=True)
            subprocess.run(
                _cmd(sudo, "tee", str(NEBULA_CONF)),
                input=nebula_conf, text=True, check=True, capture_output=True,
            )
            results.append(f"Written {NEBULA_CONF}")

        subprocess.run(_cmd(sudo, "netplan", "apply"), check=True, capture_output=True, text=True)
        results.append("netplan apply: OK")

        # DoH: bring dnsproxy up *before* dnsmasq restarts (dnsmasq's doh
        # upstream is 127.0.0.1:5053) and *before* the iptables script runs
        # (its health determines whether the :53 block below is safe to
        # activate). Order matters: dnsproxy up → dnsmasq restart → iptables.
        router_cfg = state.get("router", {})
        doh_mode = router_cfg.get("wan_dns_mode") == "doh"
        doh_healthy = False
        if doh_mode and doh_conf:
            subprocess.run(
                _cmd(sudo, "tee", str(DNSPROXY_CONF)),
                input=doh_conf, text=True, check=True, capture_output=True,
            )
            results.append(f"Written {DNSPROXY_CONF}")
            subprocess.run(_cmd(sudo, "systemctl", "enable", "--now", "dnsproxy-doh"), check=True, capture_output=True, text=True)
            subprocess.run(_cmd(sudo, "systemctl", "restart", "dnsproxy-doh"), check=True, capture_output=True, text=True)
            doh_healthy = dnsproxy_healthy()
            results.append("dnsproxy-doh restart: OK" if doh_healthy else "dnsproxy-doh restart: started but not healthy")
        else:
            subprocess.run(_cmd(sudo, "systemctl", "stop", "dnsproxy-doh"), check=False, capture_output=True, text=True)
            subprocess.run(_cmd(sudo, "systemctl", "disable", "dnsproxy-doh"), check=False, capture_output=True, text=True)

        subprocess.run(_cmd(sudo, "systemctl", "restart", "dnsmasq"), check=True, capture_output=True, text=True)
        results.append("dnsmasq restart: OK")

        subprocess.run(_cmd(sudo, "systemctl", "restart", "rsyslog"), check=True, capture_output=True, text=True)
        results.append("rsyslog restart: OK")

        # Fail-safe: if DoH is enabled with the :53 block requested but
        # dnsproxy didn't come up healthy, regenerate iptables with the
        # block forced off rather than leaving the LAN with no working DNS
        # at all (dnsmasq's only upstream in doh mode is the proxy we just
        # confirmed isn't healthy).
        active_ipt = ipt
        if doh_mode and router_cfg.get("block_wan_dns") and not doh_healthy:
            safe_state = dict(state)
            safe_state["router"] = dict(router_cfg, block_wan_dns=False)
            active_ipt = iptables.generate(safe_state)
            results.append(
                "⚠ DoH proxy unhealthy — outbound :53 block was NOT applied "
                "to avoid a DNS outage"
            )

        # Write iptables script directly (/etc/spud-router/ is service-user writable)
        IPTABLES_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
        IPTABLES_SCRIPT.write_text(active_ipt)
        IPTABLES_SCRIPT.chmod(0o750)
        results.append(f"Written {IPTABLES_SCRIPT}")

        proc = subprocess.run(_cmd(sudo, "bash", str(IPTABLES_SCRIPT)), check=True, capture_output=True, text=True)
        if proc.stderr.strip():
            results.append(f"iptables: OK (stderr: {proc.stderr.strip()})")
        else:
            results.append("iptables: OK")

        # Start or stop hostapd based on wireless enabled state
        wireless = state.get("wireless", {})
        if wireless.get("enabled") and hap:
            subprocess.run(_cmd(sudo, "systemctl", "enable", "--now", "hostapd"), check=True, capture_output=True, text=True)
            subprocess.run(_cmd(sudo, "systemctl", "restart", "hostapd"), check=True, capture_output=True, text=True)
            results.append("hostapd restart: OK")
        else:
            subprocess.run(_cmd(sudo, "systemctl", "stop", "hostapd"), check=False, capture_output=True, text=True)
            subprocess.run(_cmd(sudo, "systemctl", "disable", "hostapd"), check=False, capture_output=True, text=True)

        # Start or stop snmpd based on the snmp enabled state
        snmp = state.get("snmp", {})
        if snmp.get("enabled") and snmpc:
            subprocess.run(_cmd(sudo, "systemctl", "enable", "--now", "snmpd"), check=True, capture_output=True, text=True)
            subprocess.run(_cmd(sudo, "systemctl", "restart", "snmpd"), check=True, capture_output=True, text=True)
            results.append("snmpd restart: OK")
        else:
            subprocess.run(_cmd(sudo, "systemctl", "stop", "snmpd"), check=False, capture_output=True, text=True)
            subprocess.run(_cmd(sudo, "systemctl", "disable", "snmpd"), check=False, capture_output=True, text=True)

        results += _apply_vpn_providers(state, sudo)

    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        detail = f"Command failed: {' '.join(str(a) for a in e.cmd)} (exit {e.returncode})"
        if stderr:
            detail += f": {stderr}"
        raise RuntimeError(detail)
    except OSError as e:
        raise RuntimeError(f"File error: {e}")

    return results
