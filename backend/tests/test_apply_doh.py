"""
Tests for the DoH cloudflared apply() wiring — specifically the fail-safe
that must refuse to activate the outbound :53 block when cloudflared
didn't come up healthy, so a LAN client is never left with zero working
DNS path (dnsmasq's only upstream in doh mode is the proxy).
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import backend.state as state_module
import backend.auth as auth_module


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    conf_dir   = tmp_path / "spud-router"
    state_file = conf_dir / "state.json"
    auth_file  = conf_dir / "auth.json"
    monkeypatch.setattr(state_module, "SPUD_CONF",             conf_dir)
    monkeypatch.setattr(state_module, "STATE_FILE",            state_file)
    monkeypatch.setattr(state_module, "APPLIED_SNAPSHOT_FILE", conf_dir / "applied.json")
    monkeypatch.setattr(auth_module,  "AUTH_FILE",             auth_file)
    monkeypatch.setattr(auth_module,  "SPUD_CONF",             conf_dir)
    monkeypatch.setattr(auth_module,  "CLI_TOKEN_FILE",        conf_dir / "cli-token")
    monkeypatch.setattr(auth_module,  "TOKEN_SECRET_FILE",     conf_dir / "token-secret")
    monkeypatch.setattr(auth_module,  "_revoked",              set())

    import backend.routers.config as config_module
    monkeypatch.setattr(config_module, "APPLIED_SNAPSHOT_FILE", conf_dir / "applied.json")
    monkeypatch.setattr(config_module, "IPTABLES_SCRIPT",       conf_dir / "iptables.sh")


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def authed_client(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "spudrouter"})
    assert resp.status_code == 200
    token = resp.json()["token"]
    client.headers.update({"X-Session-Token": token})
    return client


def _run_side_effect(is_active_output: str):
    def _run(cmd, *a, **kw):
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        if cmd[:3] == ["systemctl", "is-active", "cloudflared-doh"]:
            m.stdout = is_active_output
        return m
    return _run


def _setup_doh_state(authed_client, block_wan_dns: bool):
    authed_client.post("/api/vlans", json={
        "vlan_id": 10, "name": "Trusted", "interface": "eth0",
        "ip_address": "192.168.10.1", "prefix_len": 24,
    })
    authed_client.post("/api/router", json={
        "wan_interface": "eth1", "wan_mode": "dhcp",
        "wan_dns_mode": "doh", "doh_provider": "cloudflare",
        "block_wan_dns": block_wan_dns,
    })


class TestDohHealthyPath:
    def test_block_applied_when_cloudflared_healthy(self, authed_client):
        _setup_doh_state(authed_client, block_wan_dns=True)
        with patch("backend.routers.config.subprocess.run", side_effect=_run_side_effect("active")):
            resp = authed_client.post("/api/apply", json={"dry_run": False})
        assert resp.status_code == 200
        steps = resp.json()["steps"]
        assert not any("unhealthy" in s for s in steps)

        import backend.routers.config as config_module
        ipt_content = config_module.IPTABLES_SCRIPT.read_text()
        assert "--dport 53 -j REJECT" in ipt_content


class TestDohUnhealthyFailSafe:
    def test_block_not_applied_when_cloudflared_unhealthy(self, authed_client):
        _setup_doh_state(authed_client, block_wan_dns=True)
        with patch("backend.routers.config.subprocess.run", side_effect=_run_side_effect("failed")):
            resp = authed_client.post("/api/apply", json={"dry_run": False})
        assert resp.status_code == 200
        steps = resp.json()["steps"]
        assert any("unhealthy" in s for s in steps)

        import backend.routers.config as config_module
        ipt_content = config_module.IPTABLES_SCRIPT.read_text()
        assert "-j REJECT" not in ipt_content

    def test_no_block_requested_no_warning_even_if_unhealthy(self, authed_client):
        _setup_doh_state(authed_client, block_wan_dns=False)
        with patch("backend.routers.config.subprocess.run", side_effect=_run_side_effect("failed")):
            resp = authed_client.post("/api/apply", json={"dry_run": False})
        assert resp.status_code == 200
        steps = resp.json()["steps"]
        assert not any("unhealthy" in s for s in steps)


class TestNonDohModeUnaffected:
    def test_non_doh_mode_never_touches_cloudflared_health(self, authed_client):
        authed_client.post("/api/router", json={
            "wan_interface": "eth1", "wan_mode": "dhcp", "wan_dns_mode": "manual",
        })
        # Patching subprocess.run through either module path mutates the
        # same real `subprocess` module attribute in place (both
        # config.py and tailscale.py did a plain `import subprocess`) — a
        # second patch() targeting it would just save-and-replace the
        # first mock rather than layering, so a single patch here already
        # covers every subprocess.run call apply() makes, tailscale
        # included.
        with patch("backend.routers.config.subprocess.run", side_effect=_run_side_effect("failed")) as mock_run:
            resp = authed_client.post("/api/apply", json={"dry_run": False})
        assert resp.status_code == 200
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert not any(c[:2] == ["systemctl", "is-active"] for c in calls)
        assert any(c[:3] == ["sudo", "systemctl", "stop"] and "cloudflared-doh" in c for c in calls)
