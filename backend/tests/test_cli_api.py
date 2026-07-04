# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Regression tests for the spud-cli API client.

The backend serves HTTPS with a self-signed cert, so the CLI must talk https
(not http) and must not verify the cert. This was broken (API_BASE was http://
and urllib used default verification), which made the TUI unusable for the
'spud' user — every call failed with RemoteDisconnected / CERTIFICATE_VERIFY_
FAILED and it got stuck in an unrecoverable login loop.
"""
import http.server
import json
import shutil
import ssl
import subprocess
import threading

import pytest

import cli.api as api


def test_api_base_is_https():
    # http:// to the TLS port is refused ("RemoteDisconnected"); the base URL
    # must be https so the CLI reaches the backend at all.
    assert api.API_BASE.startswith("https://")


class _JSONHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # silence test output
        pass


@pytest.fixture
def self_signed_https_server(tmp_path):
    """A minimal HTTPS server presenting a self-signed cert, like the box does."""
    if not shutil.which("openssl"):
        pytest.skip("openssl not available to generate a self-signed cert")
    key = tmp_path / "key.pem"
    crt = tmp_path / "crt.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(crt), "-days", "1", "-subj", "/CN=localhost"],
        check=True, capture_output=True,
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _JSONHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()


def test_cli_connects_over_https_to_self_signed_cert(self_signed_https_server, monkeypatch):
    # Point the CLI client at the self-signed HTTPS server and confirm a real
    # request succeeds. Fails on the old code: http scheme → RemoteDisconnected,
    # and a verifying context → CERTIFICATE_VERIFY_FAILED.
    monkeypatch.setattr(api, "API_BASE", self_signed_https_server)
    monkeypatch.setattr(api, "_token", None)
    result = api.GET("/api/health")
    assert result["status"] == "ok"
    assert result["path"] == "/api/health"
