# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
API tests for GET/PUT /api/nebula and POST/DELETE /api/nebula/credentials.

The `nebula-cert`/`nebula` CLIs are mocked throughout (they aren't
installed on the test machine) via a fake bound only onto
`backend.routers.nebula.subprocess` — see _FakeNebulaTools's docstring for
why a naive blanket subprocess.run replacement is the wrong tool here
(same class of gotcha documented in test_wireguard_api.py and
test_tls_api.py).
"""
import json

import pytest
from fastapi.testclient import TestClient

import backend.state as state_module
import backend.auth as auth_module
import backend.routers.nebula as nebula_router

VALID_CERT = "-----BEGIN NEBULA CERTIFICATE-----\nabc\n-----END NEBULA CERTIFICATE-----\n"
VALID_KEY  = "-----BEGIN NEBULA ED25519 PRIVATE KEY-----\nabc\n-----END NEBULA ED25519 PRIVATE KEY-----\n"
VALID_CA   = "-----BEGIN NEBULA CERTIFICATE-----\ndef\n-----END NEBULA CERTIFICATE-----\n"


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeNebulaTools:
    """
    Deterministic stand-in for `nebula-cert verify`, `nebula-cert print
    -json`, and `nebula -test`. Configurable per-test via `verify_ok`,
    `test_ok`, and `cert_details` so both the happy path and each
    rejection branch (bad CA signature, expired cert, mismatched
    key/config) can be exercised without a real binary.
    """
    def __init__(self, verify_ok=True, test_ok=True, cert_details=None):
        self.verify_ok = verify_ok
        self.test_ok = test_ok
        self.cert_details = cert_details or {
            "name": "host1", "ips": ["192.168.100.2/24"], "groups": ["clients"],
            "issuer": "deadbeef", "notBefore": "2024-01-01T00:00:00Z",
            "notAfter": "2099-01-01T00:00:00Z", "isCa": False,
        }
        self.calls = []

    def run(self, cmd, *a, **k):
        self.calls.append(cmd)
        if cmd[:2] == ["nebula-cert", "verify"]:
            return _Result(0 if self.verify_ok else 1, stderr="" if self.verify_ok else "signature mismatch")
        if cmd[:2] == ["nebula-cert", "print"]:
            return _Result(0, stdout=json.dumps({"details": self.cert_details}))
        if cmd[:2] == ["nebula", "-test"]:
            return _Result(0 if self.test_ok else 1, stderr="" if self.test_ok else "key does not match cert")
        return _Result(1, stderr=f"unexpected command: {cmd}")


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

    fake = _FakeNebulaTools()
    monkeypatch.setattr(nebula_router.subprocess, "run", fake.run)
    return {"fake": fake}


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


class TestGetConfig:
    def test_default_disabled_no_key(self, authed_client):
        resp = authed_client.get("/api/nebula")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert body["has_key"] is False
        assert body["key_pem"] == ""
        assert body["cert_info"] is None
        assert body["ca_info"] is None

    def test_requires_auth(self, client):
        assert client.get("/api/nebula").status_code == 401


class TestSetConfig:
    def test_updates_settings_fields(self, authed_client):
        resp = authed_client.put("/api/nebula", json={
            "enabled": True, "listen_port": 5555,
            "lighthouse_hosts": ["192.168.100.1"],
            "static_host_map": {"192.168.100.1": ["lh.example.com:4242"]},
        })
        assert resp.status_code == 200
        got = authed_client.get("/api/nebula").json()
        assert got["listen_port"] == 5555
        assert got["lighthouse_hosts"] == ["192.168.100.1"]

    def test_cannot_set_credentials_via_put(self, authed_client, isolated_env):
        isolated_env["fake"].verify_ok = True
        isolated_env["fake"].test_ok = True
        authed_client.post("/api/nebula/credentials", json={
            "cert_pem": VALID_CERT, "key_pem": VALID_KEY, "ca_pem": VALID_CA,
        })
        first_cert_info = authed_client.get("/api/nebula").json()["cert_info"]

        # A settings-only PUT must not be able to swap out the stored
        # credentials, even with well-formed (but different) PEM values.
        other_cert = "-----BEGIN NEBULA CERTIFICATE-----\nzzz\n-----END NEBULA CERTIFICATE-----\n"
        resp = authed_client.put("/api/nebula", json={
            "enabled": True, "cert_pem": other_cert, "key_pem": other_cert, "ca_pem": other_cert,
        })
        assert resp.status_code == 200
        got = authed_client.get("/api/nebula").json()
        assert got["has_key"] is True  # unchanged from the earlier import
        assert got["cert_info"] == first_cert_info

    def test_invalid_listen_port_rejected(self, authed_client):
        resp = authed_client.put("/api/nebula", json={"listen_port": 0})
        assert resp.status_code == 422

    def test_invalid_lighthouse_host_rejected(self, authed_client):
        resp = authed_client.put("/api/nebula", json={"lighthouse_hosts": ["not-an-ip"]})
        assert resp.status_code == 422

    def test_requires_auth(self, client):
        assert client.put("/api/nebula", json={}).status_code == 401


class TestCredentials:
    def test_valid_credentials_accepted(self, authed_client):
        resp = authed_client.post("/api/nebula/credentials", json={
            "cert_pem": VALID_CERT, "key_pem": VALID_KEY, "ca_pem": VALID_CA,
        })
        assert resp.status_code == 200
        assert resp.json()["cert_info"]["name"] == "host1"

        got = authed_client.get("/api/nebula").json()
        assert got["has_key"] is True
        assert got["key_pem"] == "********"
        assert got["cert_info"]["name"] == "host1"
        assert got["ca_info"] is not None

    def test_private_key_never_returned(self, authed_client):
        resp = authed_client.post("/api/nebula/credentials", json={
            "cert_pem": VALID_CERT, "key_pem": VALID_KEY, "ca_pem": VALID_CA,
        })
        assert VALID_KEY not in json.dumps(resp.json())

    def test_ca_verification_failure_rejected(self, authed_client, isolated_env):
        isolated_env["fake"].verify_ok = False
        resp = authed_client.post("/api/nebula/credentials", json={
            "cert_pem": VALID_CERT, "key_pem": VALID_KEY, "ca_pem": VALID_CA,
        })
        assert resp.status_code == 400
        assert "CA verification" in resp.json()["detail"]
        assert authed_client.get("/api/nebula").json()["has_key"] is False

    def test_nebula_test_failure_rejected(self, authed_client, isolated_env):
        isolated_env["fake"].test_ok = False
        resp = authed_client.post("/api/nebula/credentials", json={
            "cert_pem": VALID_CERT, "key_pem": VALID_KEY, "ca_pem": VALID_CA,
        })
        assert resp.status_code == 400
        assert "rejected" in resp.json()["detail"]
        assert authed_client.get("/api/nebula").json()["has_key"] is False

    def test_expired_cert_rejected(self, authed_client, isolated_env):
        isolated_env["fake"].cert_details = {
            "name": "host1", "notBefore": "2020-01-01T00:00:00Z", "notAfter": "2021-01-01T00:00:00Z",
        }
        resp = authed_client.post("/api/nebula/credentials", json={
            "cert_pem": VALID_CERT, "key_pem": VALID_KEY, "ca_pem": VALID_CA,
        })
        assert resp.status_code == 400
        assert "expired" in resp.json()["detail"]

    def test_malformed_pem_rejected_before_any_subprocess_call(self, authed_client, isolated_env):
        resp = authed_client.post("/api/nebula/credentials", json={
            "cert_pem": "not pem", "key_pem": VALID_KEY, "ca_pem": VALID_CA,
        })
        assert resp.status_code == 422
        assert isolated_env["fake"].calls == []

    def test_clear_credentials(self, authed_client):
        authed_client.post("/api/nebula/credentials", json={
            "cert_pem": VALID_CERT, "key_pem": VALID_KEY, "ca_pem": VALID_CA,
        })
        resp = authed_client.delete("/api/nebula/credentials")
        assert resp.status_code == 200
        got = authed_client.get("/api/nebula").json()
        assert got["has_key"] is False
        assert got["cert_info"] is None
        assert got["ca_info"] is None

    def test_requires_auth(self, client):
        assert client.post("/api/nebula/credentials", json={}).status_code == 401
        assert client.delete("/api/nebula/credentials").status_code == 401
