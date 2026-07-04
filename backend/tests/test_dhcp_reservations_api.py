# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
API tests for per-VLAN DHCP reservation CRUD:
  GET/POST   /api/vlans/{vlan_id}/reservations
  PUT/DELETE /api/vlans/{vlan_id}/reservations/{reservation_id}
"""
import pytest
from fastapi.testclient import TestClient

import backend.state as state_module
import backend.auth as auth_module


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirect all state I/O and auth to temp dirs for every test."""
    conf_dir   = tmp_path / "spud-router"
    state_file = conf_dir / "state.json"
    auth_file  = conf_dir / "auth.json"
    monkeypatch.setattr(state_module, "SPUD_CONF",         conf_dir)
    monkeypatch.setattr(state_module, "STATE_FILE",        state_file)
    monkeypatch.setattr(auth_module,  "AUTH_FILE",         auth_file)
    monkeypatch.setattr(auth_module,  "SPUD_CONF",         conf_dir)
    monkeypatch.setattr(auth_module,  "CLI_TOKEN_FILE",    conf_dir / "cli-token")
    monkeypatch.setattr(auth_module,  "TOKEN_SECRET_FILE", conf_dir / "token-secret")
    monkeypatch.setattr(auth_module,  "_revoked",          set())


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


@pytest.fixture
def vlan_10(authed_client):
    """Create VLAN 10 (192.168.10.0/24) via the API and return its id."""
    resp = authed_client.post("/api/vlans", json={
        "vlan_id": 10, "name": "Trusted", "interface": "eth0",
        "ip_address": "192.168.10.1", "prefix_len": 24,
        "dhcp_enabled": True, "dhcp_start": "192.168.10.100",
        "dhcp_end": "192.168.10.200", "dhcp_lease": "12h", "isolate": False,
    })
    assert resp.status_code == 200
    return 10


class TestListReservations:
    def test_empty_by_default(self, authed_client, vlan_10):
        resp = authed_client.get(f"/api/vlans/{vlan_10}/reservations")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_unknown_vlan_404(self, authed_client):
        resp = authed_client.get("/api/vlans/999/reservations")
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        # Auth is enforced before the VLAN lookup, so no VLAN needs to exist.
        assert client.get("/api/vlans/10/reservations").status_code == 401


class TestAddReservation:
    def test_add_happy_path(self, authed_client, vlan_10):
        resp = authed_client.post(f"/api/vlans/{vlan_10}/reservations", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.50", "hostname": "printer",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["id"]

        listed = authed_client.get(f"/api/vlans/{vlan_10}/reservations").json()
        assert len(listed) == 1
        assert listed[0]["mac"] == "aa:bb:cc:dd:ee:ff"
        assert listed[0]["ip"] == "192.168.10.50"
        assert listed[0]["hostname"] == "printer"
        assert listed[0]["id"] == body["id"]

    def test_unknown_vlan_404(self, authed_client):
        resp = authed_client.post("/api/vlans/999/reservations", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.50",
        })
        assert resp.status_code == 404

    def test_ip_outside_subnet_rejected(self, authed_client, vlan_10):
        resp = authed_client.post(f"/api/vlans/{vlan_10}/reservations", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.50",
        })
        assert resp.status_code == 400
        assert "subnet" in resp.json()["detail"]

    def test_duplicate_mac_in_same_vlan_rejected(self, authed_client, vlan_10):
        authed_client.post(f"/api/vlans/{vlan_10}/reservations", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.50",
        })
        resp = authed_client.post(f"/api/vlans/{vlan_10}/reservations", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.51",
        })
        assert resp.status_code == 400
        assert "already reserved" in resp.json()["detail"]

    def test_duplicate_ip_in_same_vlan_rejected(self, authed_client, vlan_10):
        authed_client.post(f"/api/vlans/{vlan_10}/reservations", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.50",
        })
        resp = authed_client.post(f"/api/vlans/{vlan_10}/reservations", json={
            "mac": "aa:bb:cc:dd:ee:00", "ip": "192.168.10.50",
        })
        assert resp.status_code == 400
        assert "already reserved" in resp.json()["detail"]

    def test_mac_normalized_case_treated_as_duplicate(self, authed_client, vlan_10):
        authed_client.post(f"/api/vlans/{vlan_10}/reservations", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.50",
        })
        resp = authed_client.post(f"/api/vlans/{vlan_10}/reservations", json={
            "mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.10.51",
        })
        assert resp.status_code == 400

    def test_same_mac_different_vlan_allowed(self, authed_client, vlan_10):
        authed_client.post("/api/vlans", json={
            "vlan_id": 20, "name": "IoT", "interface": "eth0",
            "ip_address": "192.168.20.1", "prefix_len": 24,
            "dhcp_enabled": True, "dhcp_start": "192.168.20.100",
            "dhcp_end": "192.168.20.200", "dhcp_lease": "12h", "isolate": True,
        })
        authed_client.post(f"/api/vlans/{vlan_10}/reservations", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.50",
        })
        resp = authed_client.post("/api/vlans/20/reservations", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.20.50",
        })
        assert resp.status_code == 200

    def test_invalid_mac_rejected(self, authed_client, vlan_10):
        resp = authed_client.post(f"/api/vlans/{vlan_10}/reservations", json={
            "mac": "not-a-mac", "ip": "192.168.10.50",
        })
        assert resp.status_code == 422

    def test_requires_auth(self, client):
        resp = client.post("/api/vlans/10/reservations", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.50",
        })
        assert resp.status_code == 401


class TestUpdateReservation:
    def test_update_happy_path(self, authed_client, vlan_10):
        add_resp = authed_client.post(f"/api/vlans/{vlan_10}/reservations", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.50", "hostname": "printer",
        })
        res_id = add_resp.json()["id"]

        resp = authed_client.put(f"/api/vlans/{vlan_10}/reservations/{res_id}", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.60", "hostname": "printer2",
        })
        assert resp.status_code == 200

        listed = authed_client.get(f"/api/vlans/{vlan_10}/reservations").json()
        assert len(listed) == 1
        assert listed[0]["ip"] == "192.168.10.60"
        assert listed[0]["hostname"] == "printer2"
        assert listed[0]["id"] == res_id

    def test_update_excludes_self_from_uniqueness_check(self, authed_client, vlan_10):
        add_resp = authed_client.post(f"/api/vlans/{vlan_10}/reservations", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.50",
        })
        res_id = add_resp.json()["id"]

        # Re-submitting the same MAC/IP for the same reservation must not
        # trip the duplicate check against itself.
        resp = authed_client.put(f"/api/vlans/{vlan_10}/reservations/{res_id}", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.50", "description": "updated",
        })
        assert resp.status_code == 200

    def test_update_unknown_reservation_404(self, authed_client, vlan_10):
        resp = authed_client.put(f"/api/vlans/{vlan_10}/reservations/deadbeef", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.50",
        })
        assert resp.status_code == 404

    def test_update_unknown_vlan_404(self, authed_client):
        resp = authed_client.put("/api/vlans/999/reservations/deadbeef", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.50",
        })
        assert resp.status_code == 404


class TestDeleteReservation:
    def test_delete_happy_path(self, authed_client, vlan_10):
        add_resp = authed_client.post(f"/api/vlans/{vlan_10}/reservations", json={
            "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.10.50",
        })
        res_id = add_resp.json()["id"]

        resp = authed_client.delete(f"/api/vlans/{vlan_10}/reservations/{res_id}")
        assert resp.status_code == 200
        assert resp.json()["removed"] == 1
        assert authed_client.get(f"/api/vlans/{vlan_10}/reservations").json() == []

    def test_delete_unknown_reservation_removes_nothing(self, authed_client, vlan_10):
        resp = authed_client.delete(f"/api/vlans/{vlan_10}/reservations/deadbeef")
        assert resp.status_code == 200
        assert resp.json()["removed"] == 0

    def test_delete_unknown_vlan_404(self, authed_client):
        resp = authed_client.delete("/api/vlans/999/reservations/deadbeef")
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        assert client.delete("/api/vlans/10/reservations/deadbeef").status_code == 401
