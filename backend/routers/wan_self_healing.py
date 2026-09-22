# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Configuration and read-only status for the root-owned WAN watchdog."""
import json
from pathlib import Path

from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..models import WanSelfHealingConfig
from ..state import load_state, save_state

STATUS_FILE = Path("/var/lib/spud-router/wan-watchdog-status.json")
router = APIRouter(prefix="/api/wan-self-healing", tags=["wan self healing"], dependencies=[Depends(require_auth)])


@router.get("")
def get_config():
    return load_state().get("wan_self_healing", WanSelfHealingConfig().model_dump())


@router.put("")
def set_config(config: WanSelfHealingConfig):
    state = load_state()
    state["wan_self_healing"] = config.model_dump()
    save_state(state)
    return {"ok": True}


@router.get("/status")
def get_status():
    try:
        return json.loads(STATUS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"state": "unknown", "message": "No watchdog run has been recorded yet.", "history": []}
