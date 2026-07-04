# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Tests for state.py

State management is I/O-bound, so these tests use tmp_path to isolate
filesystem writes from the real /etc/spud-router directory.
"""
import json
import pytest
from unittest.mock import patch
from pathlib import Path

import state as state_module
from state import empty_state, load_state, save_state


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirect all state I/O to a temp directory for every test."""
    conf_dir   = tmp_path / "spud-router"
    state_file = conf_dir / "state.json"
    monkeypatch.setattr(state_module, "SPUD_CONF",  conf_dir)
    monkeypatch.setattr(state_module, "STATE_FILE", state_file)
    return conf_dir


class TestEmptyState:
    def test_has_all_required_keys(self):
        s = empty_state()
        for key in ("vlans", "router", "static_routes", "dns_entries",
                    "fw_inbound", "fw_intervlan", "tailscale"):
            assert key in s

    def test_vlans_is_empty_list(self):
        assert empty_state()["vlans"] == []

    def test_tailscale_enabled_is_false(self):
        assert empty_state()["tailscale"]["enabled"] is False


class TestLoadState:
    def test_returns_empty_state_when_file_missing(self):
        s = load_state()
        assert s["vlans"] == []

    def test_loads_written_state(self, tmp_path):
        data = empty_state()
        data["vlans"] = [{"vlan_id": 10, "name": "Test"}]
        save_state(data)
        loaded = load_state()
        assert loaded["vlans"][0]["vlan_id"] == 10

    def test_backfills_missing_keys(self, tmp_path):
        """Older state files missing new keys get defaults backfilled."""
        data = {"vlans": [], "router": {}}  # missing fw_inbound etc.
        save_state(data)
        loaded = load_state()
        assert "fw_inbound" in loaded
        assert "fw_intervlan" in loaded
        assert "dns_entries" in loaded

    def test_returns_empty_on_corrupt_json(self, isolated_state):
        state_file = isolated_state / "state.json"
        isolated_state.mkdir(parents=True, exist_ok=True)
        state_file.write_text("{ this is not valid json }")
        s = load_state()
        assert s == empty_state()


class TestSaveState:
    def test_creates_directory_if_missing(self, isolated_state):
        assert not isolated_state.exists()
        save_state(empty_state())
        assert isolated_state.exists()

    def test_written_file_is_valid_json(self):
        save_state(empty_state())
        from state import STATE_FILE
        text = STATE_FILE.read_text()
        data = json.loads(text)
        assert "vlans" in data

    def test_roundtrip(self):
        original = empty_state()
        original["vlans"] = [{"vlan_id": 42, "name": "roundtrip"}]
        save_state(original)
        loaded = load_state()
        assert loaded["vlans"][0]["vlan_id"] == 42

    def test_atomic_write_uses_temp_file(self, isolated_state, monkeypatch):
        """save_state should write to a .tmp file then rename — verify no data loss
        if interrupted by checking the rename happens."""
        renamed = []
        original_rename = Path.rename

        def tracking_rename(self, target):
            renamed.append((str(self), str(target)))
            return original_rename(self, target)

        monkeypatch.setattr(Path, "rename", tracking_rename)
        save_state(empty_state())
        assert len(renamed) == 1
        src, dst = renamed[0]
        assert src.endswith(".tmp")
        assert not src.endswith(".tmp.tmp")
