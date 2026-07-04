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
            "netplan", "dnsmasq", "iptables", "hostapd", "syslog", "snmp", "cloudflared",
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
        assert result["cloudflared"] == ""


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
