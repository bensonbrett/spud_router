# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Unit tests for backend/web_ui_guard.py — the web UI (tcp/8080)
all-interfaces-off lockout guard (#209).
"""
import pytest

import backend.web_ui_guard as web_ui_guard


class TestWebUiReachable:
    def test_empty_state_not_reachable(self):
        assert web_ui_guard.web_ui_reachable({}) is False

    def test_mgmt_enabled_default_web_ui_reachable(self):
        state = {"router": {"mgmt_enabled": True}, "vlans": []}
        assert web_ui_guard.web_ui_reachable(state) is True

    def test_mgmt_enabled_web_ui_explicitly_off_not_reachable(self):
        state = {"router": {"mgmt_enabled": True, "mgmt_web_ui": False}, "vlans": []}
        assert web_ui_guard.web_ui_reachable(state) is False

    def test_mgmt_disabled_ignored_even_if_web_ui_true(self):
        state = {"router": {"mgmt_enabled": False, "mgmt_web_ui": True}, "vlans": []}
        assert web_ui_guard.web_ui_reachable(state) is False

    def test_lan_vlan_default_web_ui_reachable(self):
        state = {"router": {}, "vlans": [{"ip_address": "192.168.10.1", "vlan_id": 10}]}
        assert web_ui_guard.web_ui_reachable(state) is True

    def test_lan_vlan_web_ui_off_not_reachable(self):
        state = {"router": {}, "vlans": [{"ip_address": "192.168.10.1", "vlan_id": 10, "web_ui": False}]}
        assert web_ui_guard.web_ui_reachable(state) is False

    def test_wan_marker_vlan_without_ip_ignored(self):
        """A VLAN entry with no ip_address (WAN marker) never counts,
        regardless of its own web_ui value."""
        state = {"router": {}, "vlans": [{"ip_address": "", "vlan_id": 2, "web_ui": True}]}
        assert web_ui_guard.web_ui_reachable(state) is False

    def test_one_of_several_vlans_on_is_enough(self):
        state = {"router": {}, "vlans": [
            {"ip_address": "192.168.10.1", "vlan_id": 10, "web_ui": False},
            {"ip_address": "192.168.20.1", "vlan_id": 20, "web_ui": True},
        ]}
        assert web_ui_guard.web_ui_reachable(state) is True

    def test_mgmt_and_all_vlans_off_not_reachable(self):
        state = {
            "router": {"mgmt_enabled": True, "mgmt_web_ui": False},
            "vlans": [
                {"ip_address": "192.168.10.1", "vlan_id": 10, "web_ui": False},
                {"ip_address": "192.168.20.1", "vlan_id": 20, "web_ui": False},
            ],
        }
        assert web_ui_guard.web_ui_reachable(state) is False


class TestValidateWebUiReachable:
    def test_unconfigured_state_not_rejected(self):
        """No mgmt, no VLANs yet (e.g. mid initial setup, WAN-only so
        far) — nothing to be locked out of, so this must NOT raise."""
        web_ui_guard.validate_web_ui_reachable({"router": {}, "vlans": []})

    def test_mgmt_only_state_web_ui_off_rejected(self):
        state = {"router": {"mgmt_enabled": True, "mgmt_web_ui": False}, "vlans": []}
        with pytest.raises(ValueError, match="every interface"):
            web_ui_guard.validate_web_ui_reachable(state)

    def test_single_vlan_web_ui_off_no_mgmt_rejected(self):
        state = {"router": {"mgmt_enabled": False}, "vlans": [
            {"ip_address": "192.168.10.1", "vlan_id": 10, "web_ui": False},
        ]}
        with pytest.raises(ValueError, match="every interface"):
            web_ui_guard.validate_web_ui_reachable(state)

    def test_all_off_across_mgmt_and_vlans_rejected(self):
        state = {
            "router": {"mgmt_enabled": True, "mgmt_web_ui": False},
            "vlans": [
                {"ip_address": "192.168.10.1", "vlan_id": 10, "web_ui": False},
                {"ip_address": "192.168.20.1", "vlan_id": 20, "web_ui": False},
            ],
        }
        with pytest.raises(ValueError):
            web_ui_guard.validate_web_ui_reachable(state)

    def test_at_least_one_on_accepted(self):
        state = {
            "router": {"mgmt_enabled": True, "mgmt_web_ui": False},
            "vlans": [{"ip_address": "192.168.10.1", "vlan_id": 10, "web_ui": True}],
        }
        web_ui_guard.validate_web_ui_reachable(state)  # must not raise

    def test_default_missing_fields_accepted(self):
        """Backward compat: state.json missing web_ui/mgmt_web_ui entirely
        (pre-#209) behaves as if both were True — never rejected."""
        state = {
            "router": {"mgmt_enabled": True},
            "vlans": [{"ip_address": "192.168.10.1", "vlan_id": 10}],
        }
        web_ui_guard.validate_web_ui_reachable(state)  # must not raise
