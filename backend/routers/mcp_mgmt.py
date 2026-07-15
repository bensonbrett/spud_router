# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""MCP server management routes.

The MCP server uses stdio transport — it is spawned as a subprocess by MCP
clients (Claude Desktop, VS Code Cline, etc.) via SSH. It does not run as
a standalone daemon. The management endpoints here handle configuration
only (API key generation, status, config read/delete).
"""
import json
import os

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_session_token
from ..models import (
    McpConfigResponse, McpConfigUpdateRequest, McpStatusResponse,
)
from ..state import SPUD_CONF

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


MCP_CONFIG_FILE = SPUD_CONF / "mcp-config.json"


def _load_mcp_config() -> dict | None:
    if not MCP_CONFIG_FILE.exists():
        return None
    try:
        return json.loads(MCP_CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


@router.get("/status", response_model=McpStatusResponse)
def get_mcp_status(_auth=Depends(require_session_token)):
    """Return current MCP server configuration status.

    The MCP server is spawned by MCP clients as a subprocess (stdin/stdout
    JSON-RPC), not run as a background daemon. 'running' is always False.
    """
    config = _load_mcp_config()
    if not config:
        return McpStatusResponse(configured=False, running=False)

    api_key_id = config.get("api_key", "")[10:18] if config.get("api_key", "").startswith("spud_") else None
    return McpStatusResponse(
        configured=True,
        running=False,
        read_only=config.get("read_only", False),
        api_key_id=api_key_id,
    )


@router.get("/config", response_model=McpConfigResponse)
def get_mcp_config(_auth=Depends(require_session_token)):
    """Return current MCP configuration (masked)."""
    config = _load_mcp_config()
    if not config:
        return McpConfigResponse(configured=False)

    api_key = config.get("api_key", "")
    api_key_id = api_key[10:18] if api_key.startswith("spud_") else None

    return McpConfigResponse(
        configured=True,
        base_url=config.get("base_url", "https://127.0.0.1:8080"),
        tls_verify=config.get("tls_verify", False),
        read_only=config.get("read_only", False),
        confirm_window_seconds=config.get("confirm_window_seconds", 120),
        api_key_id=api_key_id,
    )


@router.post("/enable")
def enable_mcp(_auth=Depends(require_session_token)):
    """Enable MCP server with auto-generated API key."""
    from .. import api_keys

    plaintext, stored = api_keys.create_key(
        name="mcp-server",
        scopes=["read", "write", "apply", "diagnostics", "vpn"],
        expires_at=None,
    )

    # Defaults come from McpConfigUpdateRequest so /enable and
    # PUT /api/mcp/config agree on one source of truth for them.
    defaults = McpConfigUpdateRequest()
    data = {
        "api_key_id": stored["id"],
        "api_key": plaintext,
        "base_url": defaults.base_url,
        "tls_verify": defaults.tls_verify,
        "read_only": defaults.read_only,
        "confirm_window_seconds": defaults.confirm_window_seconds,
    }

    SPUD_CONF.mkdir(parents=True, exist_ok=True)
    tmp = MCP_CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.rename(MCP_CONFIG_FILE)

    return {
        "ok": True,
        "api_key_id": stored["id"],
        "key": plaintext,
        "configured": True,
    }


@router.put("/config", response_model=McpConfigResponse)
def update_mcp_config(req: McpConfigUpdateRequest, _auth=Depends(require_session_token)):
    """Edit an already-enabled MCP config's base_url/tls_verify/read_only/
    confirm_window_seconds in place. Never rotates the API key — use
    POST /enable (or a dedicated regenerate route) for that."""
    config = _load_mcp_config()
    if not config:
        raise HTTPException(status_code=404, detail="MCP is not configured yet — enable it first.")

    config["base_url"] = req.base_url
    config["tls_verify"] = req.tls_verify
    config["read_only"] = req.read_only
    config["confirm_window_seconds"] = req.confirm_window_seconds

    SPUD_CONF.mkdir(parents=True, exist_ok=True)
    tmp = MCP_CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2))
    os.chmod(tmp, 0o600)
    tmp.rename(MCP_CONFIG_FILE)

    api_key = config.get("api_key", "")
    api_key_id = api_key[10:18] if api_key.startswith("spud_") else None
    return McpConfigResponse(
        configured=True,
        base_url=config["base_url"],
        tls_verify=config["tls_verify"],
        read_only=config["read_only"],
        confirm_window_seconds=config["confirm_window_seconds"],
        api_key_id=api_key_id,
    )


@router.delete("/config")
def delete_mcp_config(_auth=Depends(require_session_token)):
    """Delete MCP server configuration."""
    if MCP_CONFIG_FILE.exists():
        MCP_CONFIG_FILE.unlink()
    return {"ok": True}
