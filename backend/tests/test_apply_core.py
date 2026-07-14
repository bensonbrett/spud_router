# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Unit tests for backend/apply_core.py — the shared generate/activate
pipeline extracted from routers/config.py so it's reusable (fastapi-free)
by update.py's detached commit-confirm revert path.
"""
from unittest.mock import MagicMock

import pytest

# apply_core.py uses relative imports (`from . import tailscale_apply`,
# `from .generators import ...`) so it must be imported as a submodule of
# the `backend` package, not as a bare top-level module — a bare `import
# apply_core` would resolve it with no parent package and those relative
# imports would raise ImportError.
import backend.apply_core as apply_core


def _ok_run(*a, **k):
    m = MagicMock()
    m.returncode = 0
    m.stdout = ""
    m.stderr = ""
    return m


class TestGenerateAll:
    def test_returns_all_expected_keys(self, minimal_state):
        result = apply_core.generate_all(minimal_state)
        assert set(result.keys()) == {
            "netplan", "dnsmasq", "iptables", "hostapd", "syslog", "snmp", "doh",
            "bgp", "wireguard", "nebula",
        }

    def test_netplan_dnsmasq_iptables_always_strings(self, minimal_state):
        result = apply_core.generate_all(minimal_state)
        assert isinstance(result["netplan"], str)
        assert isinstance(result["dnsmasq"], str)
        assert isinstance(result["iptables"], str)

    def test_optional_generators_empty_by_default(self, minimal_state):
        result = apply_core.generate_all(minimal_state)
        assert result["hostapd"] == ""
        assert result["syslog"] == ""
        assert result["snmp"] == ""
        assert result["doh"] == ""
        assert result["bgp"] == ""
        assert result["wireguard"] == ""
        assert result["nebula"] == ""


class TestActivateAllSudoPrefixing:
    def test_sudo_true_prefixes_every_privileged_command(self, minimal_state, monkeypatch):
        calls = []
        def _record(cmd, *a, **k):
            calls.append(cmd)
            return _ok_run()
        monkeypatch.setattr(apply_core.subprocess, "run", _record)
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            monkeypatch.setattr(apply_core, "IPTABLES_SCRIPT", pathlib.Path(td) / "iptables.sh")
            apply_core.activate_all(minimal_state, sudo=True)
        # Every subprocess call except the plain iptables-health is-active
        # check should be sudo-prefixed when sudo=True.
        privileged = [c for c in calls if c[:2] != ["systemctl", "is-active"]]
        assert privileged  # sanity: we actually made calls
        assert all(c[0] == "sudo" for c in privileged)

    def test_sudo_false_never_prefixes(self, minimal_state, monkeypatch):
        calls = []
        def _record(cmd, *a, **k):
            calls.append(cmd)
            return _ok_run()
        monkeypatch.setattr(apply_core.subprocess, "run", _record)
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            monkeypatch.setattr(apply_core, "IPTABLES_SCRIPT", pathlib.Path(td) / "iptables.sh")
            apply_core.activate_all(minimal_state, sudo=False)
        assert calls
        assert all(c[0] != "sudo" for c in calls)

    def test_command_failure_raises_runtime_error_not_httpexception(self, minimal_state, monkeypatch):
        import subprocess as sp
        def _fail(cmd, *a, **k):
            raise sp.CalledProcessError(1, cmd, output="", stderr="boom")
        monkeypatch.setattr(apply_core.subprocess, "run", _fail)

        with pytest.raises(RuntimeError, match="boom"):
            apply_core.activate_all(minimal_state, sudo=True)

    def test_tailscale_apply_included_in_results(self, minimal_state, monkeypatch):
        monkeypatch.setattr(apply_core.subprocess, "run", lambda *a, **k: _ok_run())
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            monkeypatch.setattr(apply_core, "IPTABLES_SCRIPT", pathlib.Path(td) / "iptables.sh")
            results = apply_core.activate_all(minimal_state, sudo=True)
        assert any("Tailscale" in r for r in results)


class TestVpnProviderDispatch:
    """
    VPN_PROVIDERS is the whole extension point for WireGuard/Nebula (added
    in later PRs): each provider's apply() is called independently, and
    one provider raising must never prevent the others from applying —
    the admin could be connected through any one of them right now.
    """
    def test_single_provider_failure_is_isolated(self, monkeypatch):
        calls = []
        def _good(state, sudo=True):
            calls.append("good")
            return ["good: applied"]
        def _bad(state, sudo=True):
            calls.append("bad")
            raise RuntimeError("boom")

        monkeypatch.setattr(apply_core, "VPN_PROVIDERS", [("bad", _bad), ("good", _good)])
        results = apply_core._apply_vpn_providers({}, sudo=True)

        assert calls == ["bad", "good"]  # both were attempted
        assert any("good: applied" in r for r in results)
        assert any("bad apply failed" in r and "boom" in r for r in results)

    def test_all_providers_succeed_normally(self, monkeypatch):
        monkeypatch.setattr(apply_core, "VPN_PROVIDERS", [
            ("a", lambda state, sudo=True: ["a: up"]),
            ("b", lambda state, sudo=True: ["b: up"]),
        ])
        results = apply_core._apply_vpn_providers({}, sudo=True)
        assert results == ["a: up", "b: up"]

    def test_sudo_flag_forwarded_to_each_provider(self, monkeypatch):
        seen_sudo = []
        def _provider(state, sudo=True):
            seen_sudo.append(sudo)
            return []
        monkeypatch.setattr(apply_core, "VPN_PROVIDERS", [("p", _provider)])

        apply_core._apply_vpn_providers({}, sudo=False)
        assert seen_sudo == [False]


class TestFrrActivation:
    """
    Issue #177: if frr was already running-but-disabled with bgpd absent
    from the process tree (the exact state _provision_frr() can leave a
    device in after an OTA — see test_update.py's TestProvisionSystem),
    `systemctl enable --now frr` is a no-op (already active) and a
    `reload` only re-reads frr.conf, never /etc/frr/daemons — so bgpd
    would never actually spawn. activate_all() must use `restart`
    instead, which always re-execs the daemon set from /etc/frr/daemons.
    """
    def test_bgp_enabled_uses_restart_not_reload(self, minimal_state, monkeypatch):
        minimal_state["bgp"] = {"enabled": True, "asn": 65001, "router_id": "10.0.0.1"}
        calls = []
        def _record(cmd, *a, **k):
            calls.append(cmd)
            return _ok_run()
        monkeypatch.setattr(apply_core.subprocess, "run", _record)
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            monkeypatch.setattr(apply_core, "IPTABLES_SCRIPT", pathlib.Path(td) / "iptables.sh")
            apply_core.activate_all(minimal_state, sudo=False)
        frr_calls = [c for c in calls if "frr" in c]
        assert ["systemctl", "restart", "frr"] in frr_calls
        assert ["systemctl", "reload", "frr"] not in frr_calls

    def test_bgp_disabled_stops_and_disables_frr(self, minimal_state, monkeypatch):
        minimal_state["bgp"] = {"enabled": False}
        calls = []
        def _record(cmd, *a, **k):
            calls.append(cmd)
            return _ok_run()
        monkeypatch.setattr(apply_core.subprocess, "run", _record)
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            monkeypatch.setattr(apply_core, "IPTABLES_SCRIPT", pathlib.Path(td) / "iptables.sh")
            apply_core.activate_all(minimal_state, sudo=False)
        assert ["systemctl", "stop", "frr"] in calls
        assert ["systemctl", "disable", "frr"] in calls


class TestSnmpBindResolution:
    """#216 — snmp bind_interface (a NIC name) must be resolved to an IP at
    apply time; net-snmp rejects an interface name in agentAddress and snmpd
    then fails to start. Falls back to unbound when the interface has no IP."""

    def _snmp_state(self, minimal_state, bind, enabled=True):
        minimal_state["snmp"] = {
            "enabled": enabled, "version": "v2c", "community_ro": "public",
            "community_rw": "", "allowlist": ["127.0.0.1/32"],
            "bind_interface": bind, "location": "", "contact": "",
        }
        return minimal_state

    def test_bind_name_replaced_with_resolved_ip(self, minimal_state, monkeypatch):
        monkeypatch.setattr(apply_core, "_iface_ipv4", lambda n: "10.9.9.5")
        st = self._snmp_state(minimal_state, "ens19")
        resolved = apply_core._resolve_snmp_bind(st)
        assert resolved["snmp"]["bind_interface"] == "10.9.9.5"
        # the stored state keeps the NIC name — UI/API still show the choice
        assert st["snmp"]["bind_interface"] == "ens19"

    def test_generate_all_emits_ip_not_name(self, minimal_state, monkeypatch):
        monkeypatch.setattr(apply_core, "_iface_ipv4", lambda n: "10.9.9.5")
        st = self._snmp_state(minimal_state, "ens19")
        snmp_conf = apply_core.generate_all(st)["snmp"]
        assert "agentAddress udp:10.9.9.5:161" in snmp_conf
        assert "ens19" not in snmp_conf   # never the raw interface name

    def test_unresolvable_interface_falls_back_to_unbound(self, minimal_state, monkeypatch):
        monkeypatch.setattr(apply_core, "_iface_ipv4", lambda n: None)
        st = self._snmp_state(minimal_state, "ens19")
        snmp_conf = apply_core.generate_all(st)["snmp"]
        assert "agentAddress udp:161" in snmp_conf
        assert "ens19" not in snmp_conf

    def test_passthrough_when_no_bind_or_disabled(self, minimal_state, monkeypatch):
        def _boom(n):
            raise AssertionError("should not resolve when passthrough applies")
        monkeypatch.setattr(apply_core, "_iface_ipv4", _boom)
        st_nobind = self._snmp_state(minimal_state, "", enabled=True)
        assert apply_core._resolve_snmp_bind(st_nobind) is st_nobind
        st_disabled = self._snmp_state(minimal_state, "ens19", enabled=False)
        assert apply_core._resolve_snmp_bind(st_disabled) is st_disabled


class TestRsyslogDropInOrdering:
    """#217 — the drop-in must load before 50-default.conf so a keep_local=false
    '& stop' actually suppresses the local copy, and the legacy 60- file must be
    pruned so its stale rule can't double-forward."""

    def test_drop_in_sorts_before_default(self):
        assert apply_core.RSYSLOG_CONF.name < "50-default.conf"

    def test_activate_all_prunes_legacy_file(self, minimal_state, monkeypatch):
        calls = []
        def _record(cmd, *a, **k):
            calls.append(cmd)
            return _ok_run()
        monkeypatch.setattr(apply_core.subprocess, "run", _record)
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            monkeypatch.setattr(apply_core, "IPTABLES_SCRIPT", pathlib.Path(td) / "iptables.sh")
            apply_core.activate_all(minimal_state, sudo=True)
        assert ["sudo", "rm", "-f", str(apply_core.RSYSLOG_CONF_LEGACY)] in calls
