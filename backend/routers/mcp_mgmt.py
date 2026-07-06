# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""MCP server management routes."""
import json
import os
import subprocess

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..models import (
    McpConfigRequest, McpConfigResponse, McpStatusResponse,
)
from ..state import SPUD_CONF

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


MCP_CONFIG_FILE = SPUD_CONF / "mcp-config.json"
MCP_UNIT_NAME = "spud-router-mcp.service"


def _load_mcp_config() -> dict | None:
    if not MCP_CONFIG_FILE.exists():
        return None
    try:
        return json.loads(MCP_CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _is_mcp_running() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", MCP_UNIT_NAME],
            capture_output=True, text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


@router.get("/status", response_model=McpStatusResponse)
def get_mcp_status(_auth=Depends(require_auth)):
    """Return current MCP server status."""
    config = _load_mcp_config()
    if not config:
        return McpStatusResponse(configured=False, running=False)

    api_key_preview = config.get("api_key", "")[:10] + "..." if config.get("api_key") else None
    return McpStatusResponse(
        configured=True,
        running=_is_mcp_running(),
        read_only=config.get("read_only", False),
        api_key_id=api_key_preview,
    )


@router.get("/config", response_model=McpConfigResponse)
def get_mcp_config(_auth=Depends(require_auth)):
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


@router.post("/config", response_model=McpConfigResponse)
def configure_mcp(req: McpConfigRequest, _auth=Depends(require_auth)):
    """Create or update MCP server configuration."""
    data = {
        "api_key": req.api_key,
        "base_url": req.base_url,
        "tls_verify": req.tls_verify,
        "read_only": req.read_only,
        "confirm_window_seconds": req.confirm_window_seconds,
    }

    SPUD_CONF.mkdir(parents=True, exist_ok=True)
    tmp = MCP_CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.rename(MCP_CONFIG_FILE)
    os.chmod(MCP_CONFIG_FILE, 0o600)

    api_key_id = req.api_key[10:18] if req.api_key.startswith("spud_") else None
    return McpConfigResponse(
        configured=True,
        base_url=req.base_url,
        tls_verify=req.tls_verify,
        read_only=req.read_only,
        confirm_window_seconds=req.confirm_window_seconds,
        api_key_id=api_key_id,
    )


@router.post("/start")
def start_mcp(_auth=Depends(require_auth)):
    """Start the MCP server via systemd."""
    if not MCP_CONFIG_FILE.exists():
        raise HTTPException(status_code=400, detail="MCP not configured. POST /api/mcp/config first.")

    try:
        result = subprocess.run(
            ["sudo", "systemctl", "start", MCP_UNIT_NAME],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Failed to start MCP: {result.stderr.strip()}")
        return {"ok": True, "running": True}
    except subprocess.SubprocessError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop")
def stop_mcp(_auth=Depends(require_auth)):
    """Stop the MCP server via systemd."""
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "stop", MCP_UNIT_NAME],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Failed to stop MCP: {result.stderr.strip()}")
        return {"ok": True, "running": False}
    except subprocess.SubprocessError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/config")
def delete_mcp_config(_auth=Depends(require_auth)):
    """Delete MCP server configuration."""
    if _is_mcp_running():
        raise HTTPException(status_code=409, detail="Stop the MCP server before deleting configuration")

    if MCP_CONFIG_FILE.exists():
        MCP_CONFIG_FILE.unlink()
    return {"ok": True}
