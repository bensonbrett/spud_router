# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Tests for GET /api/system/monitor — memory/load/CPU/disk/interface snapshot.

The pure parsing helpers (_parse_meminfo/_parse_loadavg/_parse_cpu_line/
_parse_net_dev) are tested directly against string fixtures. The endpoint
itself is tested against monkeypatched module-level path constants
(MEMINFO_PATH etc.) mirroring how TLS_DIR/TLS_CERT are overridden in
test_tls_api.py, so these tests never depend on this machine's real /proc.
"""
import pytest
from fastapi.testclient import TestClient

import backend.state as state_module
import backend.auth as auth_module
import backend.routers.system as system_router


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    conf_dir   = tmp_path / "spud-router"
    state_file = conf_dir / "state.json"
    auth_file  = conf_dir / "auth.json"
    monkeypatch.setattr(state_module, "SPUD_CONF",  conf_dir)
    monkeypatch.setattr(state_module, "STATE_FILE", state_file)
    monkeypatch.setattr(auth_module,  "AUTH_FILE",  auth_file)
    monkeypatch.setattr(auth_module,  "SPUD_CONF",  conf_dir)
    monkeypatch.setattr(auth_module,  "CLI_TOKEN_FILE", conf_dir / "cli-token")
    monkeypatch.setattr(auth_module,  "TOKEN_SECRET_FILE", conf_dir / "token-secret")
    monkeypatch.setattr(auth_module,  "_revoked", set())

    # Point every /proc path this router reads at fixture files under
    # tmp_path, so tests are hermetic and can simulate missing files.
    monkeypatch.setattr(system_router, "MEMINFO_PATH", tmp_path / "meminfo")
    monkeypatch.setattr(system_router, "LOADAVG_PATH", tmp_path / "loadavg")
    monkeypatch.setattr(system_router, "STAT_PATH",    tmp_path / "stat")
    monkeypatch.setattr(system_router, "NET_DEV_PATH", tmp_path / "net_dev")
    monkeypatch.setattr(system_router, "DISK_PATHS", {"root": str(tmp_path)})

    return {"tmp_path": tmp_path, "conf_dir": conf_dir}


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def authed_client(client):
    """Client with a valid session token already set."""
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "spudrouter"})
    assert resp.status_code == 200
    # Extract the token from the Set-Cookie header and set it on the client
    # (TestClient uses HTTP by default, so Secure cookies aren't sent automatically)
    import re
    cookie_header = resp.headers.get("set-cookie", "")
    match = re.search(r"spud_token=([^;]+)", cookie_header)
    if match:
        client.cookies.set("spud_token", match.group(1))
    return client


MEMINFO_SAMPLE = """MemTotal:       16461176 kB
MemFree:        15208144 kB
MemAvailable:   15798708 kB
Buffers:           39792 kB
Cached:           793492 kB
SwapCached:            0 kB
SwapTotal:             0 kB
SwapFree:              0 kB
"""

MEMINFO_NO_AVAILABLE_SAMPLE = """MemTotal:       1000000 kB
MemFree:         200000 kB
Buffers:          50000 kB
Cached:          100000 kB
SwapTotal:            0 kB
SwapFree:             0 kB
"""

LOADAVG_SAMPLE = "0.27 0.17 0.09 1/107 14452\n"

STAT_SAMPLE_1 = "cpu  100 0 50 800 10 0 5 0 0 0\ncpu0 100 0 50 800 10 0 5 0 0 0\n"
STAT_SAMPLE_2 = "cpu  120 0 60 850 10 0 5 0 0 0\ncpu0 120 0 60 850 10 0 5 0 0 0\n"

NET_DEV_SAMPLE = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
    "    lo:   43403      95    0    0    0     0          0         0    43403      95    0    0    0     0       0          0\n"
    "  eth1: 16050560    8069    3   20    0     0          0         0 32892328    7727    1    0    0     0       0          0\n"
    "eth0.10: 500     10    0    0    0     0          0         0   700     12    0    0    0     0       0          0\n"
)


class TestParseMeminfo:
    def test_parses_expected_fields(self):
        result = system_router._parse_meminfo(MEMINFO_SAMPLE)
        assert result["mem_total_kb"] == 16461176
        assert result["mem_free_kb"] == 15208144
        assert result["mem_available_kb"] == 15798708
        assert result["mem_buffers_kb"] == 39792
        assert result["mem_cached_kb"] == 793492
        assert result["mem_used_kb"] == 16461176 - 15798708
        assert result["swap_total_kb"] == 0
        assert result["swap_free_kb"] == 0

    def test_falls_back_when_mem_available_missing(self):
        result = system_router._parse_meminfo(MEMINFO_NO_AVAILABLE_SAMPLE)
        assert result["mem_available_kb"] is None
        # total - free - buffers - cached = 1000000 - 200000 - 50000 - 100000
        assert result["mem_used_kb"] == 650000

    def test_raises_on_missing_required_fields(self):
        with pytest.raises(ValueError):
            system_router._parse_meminfo("SomeOtherField: 123 kB\n")


class TestParseLoadavg:
    def test_parses_three_fields(self):
        result = system_router._parse_loadavg(LOADAVG_SAMPLE)
        assert result == {"load1": 0.27, "load5": 0.17, "load15": 0.09}

    def test_raises_on_malformed_input(self):
        with pytest.raises(ValueError):
            system_router._parse_loadavg("garbage")


class TestParseCpuLine:
    def test_parses_totals(self):
        parsed = system_router._parse_cpu_line(STAT_SAMPLE_1)
        assert parsed is not None
        total, idle = parsed
        assert total == sum([100, 0, 50, 800, 10, 0, 5, 0, 0, 0])
        assert idle == 810  # idle(800) + iowait(10)

    def test_returns_none_when_cpu_line_missing(self):
        assert system_router._parse_cpu_line("cpu0 1 2 3 4\n") is None

    def test_returns_none_on_malformed_numbers(self):
        assert system_router._parse_cpu_line("cpu  a b c d\n") is None


class TestParseNetDev:
    def test_parses_known_interfaces(self):
        result = system_router._parse_net_dev(NET_DEV_SAMPLE)
        assert set(result.keys()) == {"lo", "eth1", "eth0.10"}
        assert result["eth1"] == {
            "rx_bytes": 16050560, "rx_packets": 8069, "rx_errs": 3, "rx_drop": 20,
            "tx_bytes": 32892328, "tx_packets": 7727, "tx_errs": 1, "tx_drop": 0,
        }

    def test_ignores_header_lines(self):
        result = system_router._parse_net_dev(NET_DEV_SAMPLE)
        assert "face" not in result
        assert "Inter-|   Receive" not in result

    def test_empty_input_returns_empty_dict(self):
        assert system_router._parse_net_dev("") == {}


class TestSystemMonitorEndpoint:
    def test_requires_auth(self, client):
        resp = client.get("/api/system/monitor")
        assert resp.status_code == 401

    def test_returns_expected_top_level_keys(self, authed_client, isolated_env):
        tmp_path = isolated_env["tmp_path"]
        (tmp_path / "meminfo").write_text(MEMINFO_SAMPLE)
        (tmp_path / "loadavg").write_text(LOADAVG_SAMPLE)
        (tmp_path / "stat").write_text(STAT_SAMPLE_1)
        (tmp_path / "net_dev").write_text(NET_DEV_SAMPLE)

        resp = authed_client.get("/api/system/monitor")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("memory", "load", "cpu_percent", "disks", "interfaces"):
            assert key in body

        assert body["memory"]["mem_total_kb"] == 16461176
        assert body["load"] == {"load1": 0.27, "load5": 0.17, "load15": 0.09}
        assert body["cpu_percent"] is not None
        assert "root" in body["disks"]
        assert body["disks"]["root"]["total_bytes"] > 0

    def test_reports_configured_interfaces(self, authed_client, isolated_env):
        tmp_path = isolated_env["tmp_path"]
        (tmp_path / "meminfo").write_text(MEMINFO_SAMPLE)
        (tmp_path / "loadavg").write_text(LOADAVG_SAMPLE)
        (tmp_path / "stat").write_text(STAT_SAMPLE_1)
        (tmp_path / "net_dev").write_text(NET_DEV_SAMPLE)

        state_module.save_state({
            **state_module.empty_state(),
            "router": {"wan_interface": "eth1"},
            "vlans": [{
                "vlan_id": 10, "name": "Trusted", "interface": "eth0",
                "ip_address": "192.168.10.1", "prefix_len": 24,
                "dhcp_enabled": True, "dhcp_start": "192.168.10.100",
                "dhcp_end": "192.168.10.200", "dhcp_lease": "12h", "isolate": False,
            }],
        })

        resp = authed_client.get("/api/system/monitor")
        assert resp.status_code == 200
        interfaces = resp.json()["interfaces"]
        assert "eth1" in interfaces
        assert "eth0.10" in interfaces
        assert interfaces["eth1"]["rx_bytes"] == 16050560

    def test_missing_vlan_subinterface_is_simply_omitted(self, authed_client, isolated_env):
        """A VLAN configured in state whose subinterface doesn't show up in
        /proc/net/dev (e.g. this sandbox container) must not crash the
        endpoint — it's just absent from the interfaces dict."""
        tmp_path = isolated_env["tmp_path"]
        (tmp_path / "meminfo").write_text(MEMINFO_SAMPLE)
        (tmp_path / "loadavg").write_text(LOADAVG_SAMPLE)
        (tmp_path / "stat").write_text(STAT_SAMPLE_1)
        (tmp_path / "net_dev").write_text(NET_DEV_SAMPLE)

        state_module.save_state({
            **state_module.empty_state(),
            "router": {"wan_interface": "eth1"},
            "vlans": [{
                "vlan_id": 99, "name": "Ghost", "interface": "eth9",
                "ip_address": "", "prefix_len": 24,
                "dhcp_enabled": False, "dhcp_start": "", "dhcp_end": "",
                "dhcp_lease": "12h", "isolate": False,
            }],
        })

        resp = authed_client.get("/api/system/monitor")
        assert resp.status_code == 200
        interfaces = resp.json()["interfaces"]
        assert "eth9.99" not in interfaces
        assert "eth1" in interfaces

    def test_missing_meminfo_degrades_gracefully(self, authed_client, isolated_env):
        """No /proc/meminfo (module-level path monkeypatched to a
        nonexistent file) → memory is None, not a 500."""
        tmp_path = isolated_env["tmp_path"]
        # Deliberately don't create meminfo.
        (tmp_path / "loadavg").write_text(LOADAVG_SAMPLE)
        (tmp_path / "stat").write_text(STAT_SAMPLE_1)
        (tmp_path / "net_dev").write_text(NET_DEV_SAMPLE)

        resp = authed_client.get("/api/system/monitor")
        assert resp.status_code == 200
        body = resp.json()
        assert body["memory"] is None
        assert body["load"] is not None

    def test_missing_loadavg_and_stat_degrade_gracefully(self, authed_client, isolated_env):
        tmp_path = isolated_env["tmp_path"]
        (tmp_path / "meminfo").write_text(MEMINFO_SAMPLE)
        (tmp_path / "net_dev").write_text(NET_DEV_SAMPLE)
        # No loadavg, no stat, no net_dev overrides beyond net_dev above.

        resp = authed_client.get("/api/system/monitor")
        assert resp.status_code == 200
        body = resp.json()
        assert body["load"] is None
        assert body["cpu_percent"] is None

    def test_missing_net_dev_returns_empty_interfaces(self, authed_client, isolated_env):
        tmp_path = isolated_env["tmp_path"]
        (tmp_path / "meminfo").write_text(MEMINFO_SAMPLE)
        (tmp_path / "loadavg").write_text(LOADAVG_SAMPLE)
        (tmp_path / "stat").write_text(STAT_SAMPLE_1)

        resp = authed_client.get("/api/system/monitor")
        assert resp.status_code == 200
        assert resp.json()["interfaces"] == {}

    def test_missing_disk_path_is_simply_omitted(self, authed_client, isolated_env, monkeypatch):
        tmp_path = isolated_env["tmp_path"]
        (tmp_path / "meminfo").write_text(MEMINFO_SAMPLE)
        (tmp_path / "loadavg").write_text(LOADAVG_SAMPLE)
        (tmp_path / "stat").write_text(STAT_SAMPLE_1)
        (tmp_path / "net_dev").write_text(NET_DEV_SAMPLE)
        monkeypatch.setattr(system_router, "DISK_PATHS", {
            "root": str(tmp_path), "spud_conf": str(tmp_path / "does-not-exist"),
        })

        resp = authed_client.get("/api/system/monitor")
        assert resp.status_code == 200
        disks = resp.json()["disks"]
        assert "root" in disks
        assert "spud_conf" not in disks
