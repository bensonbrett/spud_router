# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
MCP server entry point.

Usage:
    python -m backend.mcp                          # reads /etc/spud-router/mcp-config.json
    python -m backend.mcp --config ~/mcp.json       # custom config path

Reads configuration from the given config file and runs the MCP JSON-RPC 2.0
stdio server. The config file must contain an api_key and optionally a base_url,
tls_verify, read_only, and confirm_window_seconds.
"""
import argparse
import sys

from .config import McpConfig
from .server import McpServer


def main():
    parser = argparse.ArgumentParser(description="spud-router MCP server")
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="Path to MCP config JSON file (default: /etc/spud-router/mcp-config.json)",
    )
    args = parser.parse_args()

    try:
        config = McpConfig.load(path=args.config)
    except FileNotFoundError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.stderr.write("Use --config to point at a valid config file.\n")
        sys.exit(1)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)

    server = McpServer(config)
    server.run()


if __name__ == "__main__":
    main()
