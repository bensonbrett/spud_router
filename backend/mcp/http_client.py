# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
HTTP client for the MCP server.

Uses stdlib only (urllib) with bearer token auth for API key authentication.
Mirrors the pattern from backend/cli/api.py.
"""
import json
import ssl
import urllib.error
import urllib.request
from typing import Any


class HttpClient:
    def __init__(self, base_url: str, api_key: str, tls_verify: bool = False):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        if tls_verify:
            self.ssl_ctx = None
        else:
            self.ssl_ctx = ssl.create_default_context()
            self.ssl_ctx.check_hostname = False
            self.ssl_ctx.verify_mode = ssl.CERT_NONE

    def _do(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30, context=self.ssl_ctx) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                detail = json.loads(raw).get("detail", raw)
            except Exception:
                detail = raw
            raise RuntimeError(f"HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Cannot reach backend: {e.reason}")

    def get(self, path: str) -> dict[str, Any]:
        return self._do("GET", path)

    def post(self, path: str, body: dict | None = None) -> dict[str, Any]:
        return self._do("POST", path, body)

    def put(self, path: str, body: dict | None = None) -> dict[str, Any]:
        return self._do("PUT", path, body)

    def delete(self, path: str) -> dict[str, Any]:
        return self._do("DELETE", path)
