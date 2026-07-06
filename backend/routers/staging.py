# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Transactional staging pipeline routes for MCP and programmatic config."""
import json
import os
import time

from fastapi import APIRouter, Depends, HTTPException

from .. import apply_core
from ..auth import require_scope
from ..models import (
    StagingCommitResponse, StagingConfirmRequest, StagingOpRequest,
    StagingStatusResponse, StagingValidateResponse,
)
from ..staging import (
    CONFIRM_WINDOW_SECONDS, STAGING_FILE, StagingError, apply_operation,
    commit_staging, confirm_commit, validate_staging,
)
from ..state import ARM_STATUS_FILE, SPUD_CONF, load_state

STAGING_ENABLED = os.environ.get("SPUD_ENABLE_STAGING", "").lower() in ("1", "true", "yes")

router = APIRouter(prefix="/api/staging", tags=["staging"])


def _check_enabled():
    if not STAGING_ENABLED:
        raise HTTPException(status_code=501, detail="Staging pipeline is not enabled")


@router.post("/begin")
def begin(_auth=Depends(require_scope("write"))):
    """Snapshot live state into staging buffer."""
    _check_enabled()

    if STAGING_FILE.exists():
        staging = json.loads(STAGING_FILE.read_text())
        meta = staging.get("_meta", {})
        state = meta.get("state", "unknown")
        begun_at = meta.get("begun_at", 0)
        op_count = len(meta.get("operations", []))
        raise HTTPException(
            status_code=409,
            detail=f"A staging transaction is already active (begun at {begun_at}, {op_count} operations staged). Discard it first."
        )

    state = load_state()
    staging = dict(state)
    staging["_meta"] = {
        "state": "staging",
        "begun_at": time.time(),
        "operations": [],
        "validation": None,
    }

    SPUD_CONF = STAGING_FILE.parent
    SPUD_CONF.mkdir(parents=True, exist_ok=True)
    tmp = STAGING_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(staging, indent=2))
    tmp.rename(STAGING_FILE)

    return {
        "ok": True,
        "state": "staging",
        "begun_at": staging["_meta"]["begun_at"],
        "operation_count": 0,
    }


@router.post("/op")
def op(req: StagingOpRequest, _auth=Depends(require_scope("write"))):
    """Apply a single mutation to the staging buffer."""
    _check_enabled()

    if not STAGING_FILE.exists():
        raise HTTPException(status_code=409, detail="No staging transaction is active. Call POST /api/staging/begin first.")

    staging = json.loads(STAGING_FILE.read_text())
    meta = staging.get("_meta", {})
    state = meta.get("state", "idle")

    if state not in ("staging", "validated"):
        raise HTTPException(status_code=409, detail="No active staging transaction.")

    # If coming from validated state, reset to staging on new mutation
    if state == "validated":
        meta["state"] = "staging"
        meta["validation"] = None
        tmp = STAGING_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(staging, indent=2))
        tmp.rename(STAGING_FILE)

    try:
        staging = apply_operation(staging, req.op, req.data)
    except StagingError as e:
        raise HTTPException(status_code=400, detail=str(e))

    meta["operations"].append({
        "op": req.op,
        "data": req.data,
        "applied_at": time.time(),
    })

    tmp = STAGING_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(staging, indent=2))
    tmp.rename(STAGING_FILE)

    return {
        "ok": True,
        "op": req.op,
        "operation_index": len(meta["operations"]),
        "staging_state": meta["state"],
    }


@router.post("/validate", response_model=StagingValidateResponse)
def validate(_auth=Depends(require_scope("write"))):
    """Run comprehensive validation against the staged state."""
    _check_enabled()

    if not STAGING_FILE.exists():
        raise HTTPException(status_code=409, detail="No staging transaction is active. Call POST /api/staging/begin first.")

    staging = json.loads(STAGING_FILE.read_text())
    meta = staging.get("_meta", {})

    result = validate_staging(staging)
    meta["validation"] = result.to_dict()
    meta["state"] = "validated" if result.valid else "staging"

    tmp = STAGING_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(staging, indent=2))
    tmp.rename(STAGING_FILE)

    generated_preview = None
    if result.valid:
        try:
            generated_preview = apply_core.generate_all(staging)
        except Exception:
            pass

    return StagingValidateResponse(
        valid=result.valid,
        errors=result.errors,
        warnings=result.warnings,
        operation_count=len(meta.get("operations", [])),
        generated_preview=generated_preview,
    )


@router.post("/commit", response_model=StagingCommitResponse)
def commit(_auth=Depends(require_scope("apply"))):
    """Atomically promote staged state to live and activate configs."""
    _check_enabled()

    if not STAGING_FILE.exists():
        raise HTTPException(status_code=409, detail="No staging transaction is active.")

    try:
        result = commit_staging()
    except StagingError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return StagingCommitResponse(**result)


@router.post("/confirm")
def confirm(req: StagingConfirmRequest, _auth=Depends(require_scope("apply"))):
    """Cancel the auto-revert watchdog."""
    _check_enabled()

    if not confirm_commit(req.token):
        raise HTTPException(status_code=409, detail="Token does not match the currently-armed commit or nothing is armed.")
    return {"ok": True, "confirmed": True}


@router.post("/discard")
def discard(_auth=Depends(require_scope("write"))):
    """Abandon the staging transaction."""
    _check_enabled()

    if not STAGING_FILE.exists():
        return {"ok": True, "discarded_operations": 0}

    staging = json.loads(STAGING_FILE.read_text())
    meta = staging.get("_meta", {})
    op_count = len(meta.get("operations", []))

    STAGING_FILE.unlink()
    return {"ok": True, "discarded_operations": op_count}


@router.get("/status", response_model=StagingStatusResponse)
def status(_auth=Depends(require_scope("write"))):
    """Return the current staging state."""
    _check_enabled()

    if not STAGING_FILE.exists():
        return StagingStatusResponse(active=False)

    staging = json.loads(STAGING_FILE.read_text())
    meta = staging.get("_meta", {})
    state = meta.get("state", "idle")

    if state == "applied":
        if ARM_STATUS_FILE.exists():
            try:
                armed = json.loads(ARM_STATUS_FILE.read_text())
                elapsed = time.time() - armed.get("armed_at", 0)
                remaining = max(0, armed.get("window_seconds", 0) - elapsed)
                return StagingStatusResponse(
                    active=True,
                    state="applied",
                    token=armed.get("token"),
                    window_seconds=armed.get("window_seconds"),
                    armed_at=armed.get("armed_at"),
                    remaining_seconds=remaining,
                )
            except (OSError, ValueError):
                pass
        return StagingStatusResponse(active=True, state="applied")

    operations = meta.get("operations", [])
    ops_summary = []
    for op_entry in operations:
        op = op_entry.get("op", "")
        data = op_entry.get("data", {})
        if op == "add_vlan":
            ops_summary.append({"op": op, "summary": f"VLAN {data.get('vlan_id')} ({data.get('name')})"})
        elif op == "delete_vlan":
            ops_summary.append({"op": op, "summary": f"Delete VLAN {data.get('vlan_id')}"})
        elif op == "set_router":
            ops_summary.append({"op": op, "summary": "Router config"})
        elif op == "add_fw_inbound":
            ops_summary.append({"op": op, "summary": f"Add inbound rule on VLAN {data.get('vlan_id')}"})
        else:
            ops_summary.append({"op": op, "summary": op})

    return StagingStatusResponse(
        active=True,
        state=state,
        begun_at=meta.get("begun_at"),
        operation_count=len(operations),
        operations=ops_summary,
        validation=meta.get("validation"),
    )
