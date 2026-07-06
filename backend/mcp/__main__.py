# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
MCP server entry point.

Usage:
    python -m backend.mcp

Reads configuration from /etc/spud-router/mcp-config.json and runs the MCP
JSON-RPC 2.0 stdio server.
"""
import sys

from .config import McpConfig
from .server import McpServer


def main():
    try:
        config = McpConfig.load()
    except FileNotFoundError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.stderr.write("Run 'spud-cli setup-mcp' to configure the MCP server.\n")
        sys.exit(1)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)

    server = McpServer(config)
    server.run()


if __name__ == "__main__":
    main()
