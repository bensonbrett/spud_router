# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
Load and validate MCP server configuration from /etc/spud-router/mcp-config.json.

Config file format:
{
    "api_key": "spud_...",
    "base_url": "https://127.0.0.1:8080",
    "tls_verify": false,
    "read_only": false,
    "confirm_window_seconds": 120
}
"""
import json
import os
from pathlib import Path

CONFIG_PATH = Path("/etc/spud-router/mcp-config.json")


class McpConfig:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://127.0.0.1:8080",
        tls_verify: bool = False,
        read_only: bool = False,
        confirm_window_seconds: int = 120,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.tls_verify = tls_verify
        self.read_only = read_only
        self.confirm_window_seconds = confirm_window_seconds

    @classmethod
    def load(cls, path: str | Path | None = None) -> "McpConfig":
        path = Path(path) if path else CONFIG_PATH
        if not path.exists():
            raise FileNotFoundError(f"MCP config not found: {path}")

        data = json.loads(path.read_text())
        api_key = data.get("api_key", "")
        if not api_key.startswith("spud_"):
            raise ValueError("api_key must start with 'spud_'")

        return cls(
            api_key=api_key,
            base_url=data.get("base_url", "https://127.0.0.1:8080"),
            tls_verify=data.get("tls_verify", False),
            read_only=data.get("read_only", False),
            confirm_window_seconds=data.get("confirm_window_seconds", 120),
        )
