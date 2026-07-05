# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
API tests for TLS certificate management — GET/POST /api/system/tls,
POST /api/system/tls/regenerate, GET /api/system/tls/restart-status.

Real cert/key pairs are generated via openssl (already a runtime dependency)
so validation logic (parses, expiry, key-matches-cert) is exercised against
genuine PEM data rather than fixtures that might not reflect real openssl
output shapes. Only the restart-trigger call (`sudo run-update.sh
tls-restart`) is faked — via a pass-through wrapper, not a blanket mock,
since subprocess.run is one real global attribute shared by every caller in
the process; a blanket mock would also swallow the real openssl validation
calls the endpoint itself makes moments earlier in the same request.
"""
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

import backend.state as state_module
import backend.auth as auth_module
import backend.routers.system as system_router

_real_run = subprocess.run


def _ok_run(cmd, *a, **k):
    """Real subprocess.run for everything except the tls-restart trigger,
    which is faked so tests never actually invoke sudo/run-update.sh."""
    if isinstance(cmd, list) and cmd and cmd[0] == "sudo" and "tls-restart" in cmd:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return _real_run(cmd, *a, **k)


def _gen_pair(tmp_path, name="a", cn="test.example.com", days="3650"):
    cert = tmp_path / f"{name}.crt"
    key  = tmp_path / f"{name}.key"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert),
            "-days", days, "-subj", f"/CN={cn}",
        ],
        check=True, capture_output=True, text=True,
    )
    return cert.read_text(), key.read_text()


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

    tls_dir = conf_dir / "tls"
    tls_dir.mkdir(parents=True)
    run_update_script = tmp_path / "run-update.sh"
    tls_restart_status_file = tmp_path / "run-spud-router" / "tls-restart-status.json"

    monkeypatch.setattr(system_router, "TLS_DIR", tls_dir)
    monkeypatch.setattr(system_router, "TLS_CERT", tls_dir / "server.crt")
    monkeypatch.setattr(system_router, "TLS_KEY", tls_dir / "server.key")
    monkeypatch.setattr(system_router, "TLS_CERT_BAK", tls_dir / "server.crt.bak")
    monkeypatch.setattr(system_router, "TLS_KEY_BAK", tls_dir / "server.key.bak")
    monkeypatch.setattr(system_router, "RUN_UPDATE_SCRIPT", run_update_script)
    monkeypatch.setattr(system_router, "TLS_RESTART_STATUS_FILE", tls_restart_status_file)

    return {"tls_dir": tls_dir, "tls_restart_status_file": tls_restart_status_file}


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


class TestGetTls:
    def test_requires_auth(self, client):
        assert client.get("/api/system/tls").status_code == 401

    def test_404_when_no_cert_yet(self, authed_client):
        assert authed_client.get("/api/system/tls").status_code == 404

    def test_returns_parsed_fields_never_the_key(self, authed_client, isolated_env, tmp_path):
        cert, key = _gen_pair(tmp_path, cn="existing.example.com")
        (isolated_env["tls_dir"] / "server.crt").write_text(cert)
        (isolated_env["tls_dir"] / "server.key").write_text(key)

        resp = authed_client.get("/api/system/tls")
        assert resp.status_code == 200
        body = resp.json()
        assert "existing.example.com" in body["subject"]
        assert body["expired"] is False
        assert body["fingerprint_sha256"]
        assert "key" not in json.dumps(body).lower().replace("fingerprint_sha256", "")
        # the raw key material must never appear anywhere in the response
        assert key.strip() not in json.dumps(body)


class TestUploadTls:
    def test_requires_auth(self, client):
        resp = client.post("/api/system/tls", json={"cert_pem": "x", "key_pem": "y"})
        assert resp.status_code == 401

    def test_rejects_garbage_pem_at_model_layer(self, authed_client):
        resp = authed_client.post("/api/system/tls", json={"cert_pem": "nope", "key_pem": "nope"})
        assert resp.status_code == 422

    def test_valid_matching_pair_accepted(self, authed_client, isolated_env, tmp_path, monkeypatch):
        cert, key = _gen_pair(tmp_path, cn="new.example.com")
        monkeypatch.setattr(system_router.subprocess, "run", _ok_run)

        resp = authed_client.post("/api/system/tls", json={"cert_pem": cert, "key_pem": key})
        assert resp.status_code == 200
        assert resp.json() == {"restarting": True}
        assert (isolated_env["tls_dir"] / "server.crt").read_text() == cert
        assert (isolated_env["tls_dir"] / "server.key").read_text() == key
        assert oct((isolated_env["tls_dir"] / "server.key").stat().st_mode)[-3:] == "600"

    def test_mismatched_key_rejected_and_live_cert_untouched(self, authed_client, isolated_env, tmp_path, monkeypatch):
        cert_a, _key_a = _gen_pair(tmp_path, name="a", cn="a.example.com")
        _cert_b, key_b = _gen_pair(tmp_path, name="b", cn="b.example.com")
        (isolated_env["tls_dir"] / "server.crt").write_text(cert_a)
        monkeypatch.setattr(system_router.subprocess, "run", _ok_run)

        resp = authed_client.post("/api/system/tls", json={"cert_pem": cert_a, "key_pem": key_b})
        assert resp.status_code == 422
        assert "does not match" in resp.json()["detail"]
        # live cert untouched
        assert (isolated_env["tls_dir"] / "server.crt").read_text() == cert_a

    def test_expired_cert_rejected(self, authed_client, tmp_path, monkeypatch):
        # -days -1 (via -not_after trick isn't available; use checkend by
        # generating a cert that expired: openssl doesn't support negative
        # -days directly across all versions, so fake a past enddate via
        # -not_before/-not_after when supported, falling back to skip.
        proc = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                                "-keyout", str(tmp_path / "exp.key"), "-out", str(tmp_path / "exp.crt"),
                                "-days", "1", "-subj", "/CN=expired.example.com",
                                "-not_before", "20000101000000Z", "-not_after", "20000102000000Z"],
                               capture_output=True, text=True)
        if proc.returncode != 0:
            pytest.skip("openssl on this system doesn't support -not_before/-not_after")
        cert = (tmp_path / "exp.crt").read_text()
        key  = (tmp_path / "exp.key").read_text()
        resp = authed_client.post("/api/system/tls", json={"cert_pem": cert, "key_pem": key})
        assert resp.status_code == 422
        assert "expired" in resp.json()["detail"].lower()

    def test_backup_created_on_successful_upload(self, authed_client, isolated_env, tmp_path, monkeypatch):
        cert_a, key_a = _gen_pair(tmp_path, name="a", cn="a.example.com")
        cert_b, key_b = _gen_pair(tmp_path, name="b", cn="b.example.com")
        monkeypatch.setattr(system_router.subprocess, "run", _ok_run)

        authed_client.post("/api/system/tls", json={"cert_pem": cert_a, "key_pem": key_a})
        authed_client.post("/api/system/tls", json={"cert_pem": cert_b, "key_pem": key_b})

        assert (isolated_env["tls_dir"] / "server.crt.bak").read_text() == cert_a
        assert (isolated_env["tls_dir"] / "server.key.bak").read_text() == key_a
        assert (isolated_env["tls_dir"] / "server.crt").read_text() == cert_b


class TestRegenerateTls:
    def test_requires_auth(self, client):
        assert client.post("/api/system/tls/regenerate", json={}).status_code == 401

    def test_regenerate_writes_new_self_signed_pair(self, authed_client, isolated_env, monkeypatch):
        monkeypatch.setattr(system_router.subprocess, "run", _ok_run)
        resp = authed_client.post("/api/system/tls/regenerate", json={
            "common_name": "spud-router.lan", "san": ["192.168.1.1"],
        })
        assert resp.status_code == 200
        assert resp.json() == {"restarting": True}

        info_resp = authed_client.get("/api/system/tls")
        assert "spud-router.lan" in info_resp.json()["subject"]

    def test_invalid_common_name_rejected(self, authed_client):
        resp = authed_client.post("/api/system/tls/regenerate", json={"common_name": "bad cn!"})
        assert resp.status_code == 422


class TestRestartStatus:
    def test_no_file_means_none(self, authed_client):
        resp = authed_client.get("/api/system/tls/restart-status")
        assert resp.status_code == 200
        assert resp.json() == {"state": "none"}

    def test_reads_status_file(self, authed_client, isolated_env):
        isolated_env["tls_restart_status_file"].parent.mkdir(parents=True, exist_ok=True)
        isolated_env["tls_restart_status_file"].write_text(json.dumps({"state": "ok", "message": "New certificate is live."}))
        resp = authed_client.get("/api/system/tls/restart-status")
        assert resp.json()["state"] == "ok"

    def test_requires_auth(self, client):
        assert client.get("/api/system/tls/restart-status").status_code == 401
