# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""
MCP server entry point.

Usage:
    spud-router-mcp --api-key spud_xxx --base-url https://192.168.10.1:8080
    spud-router-mcp --config ~/mcp.json
    python -m backend.mcp --api-key spud_xxx --base-url https://192.168.10.1:8080

Provide either --config (pointing at a JSON file) or --api-key + --base-url
(direct connection to a remote spud-router).
"""
import argparse
import sys

from .config import McpConfig
from .server import McpServer


def main():
    parser = argparse.ArgumentParser(
        description="spud-router MCP server — run on your workstation, connect to a remote router."
    )
    parser.add_argument("--api-key", help="API key for authentication (spud_...)")
    parser.add_argument("--base-url", default="https://127.0.0.1:8080", help="Router API base URL")
    parser.add_argument(
        "--config", "-c", default=None,
        help="Path to config JSON file (alternative to --api-key/--base-url)",
    )
    parser.add_argument("--tls-verify", action="store_true", help="Verify TLS certificate")
    parser.add_argument("--read-only", action="store_true", help="Read-only mode (no mutation tools)")
    args = parser.parse_args()

    if args.config:
        try:
            config = McpConfig.load(path=args.config)
        except FileNotFoundError as e:
            sys.stderr.write(f"Error: {e}\n")
            sys.exit(1)
    elif args.api_key:
        config = McpConfig(
            api_key=args.api_key,
            base_url=args.base_url,
            tls_verify=args.tls_verify,
            read_only=args.read_only,
        )
    else:
        # Fall back to default config path
        try:
            config = McpConfig.load()
        except FileNotFoundError:
            parser.print_usage()
            sys.stderr.write("spud-router-mcp: error: provide --api-key or --config\n")
            sys.exit(1)

    server = McpServer(config)
    server.run()


if __name__ == "__main__":
    main()
