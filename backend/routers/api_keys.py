# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""API key management routes."""
from fastapi import APIRouter, Depends, HTTPException

from .. import api_keys
from ..auth import require_scope
from ..models import ApiKeyCreateRequest, ApiKeyCreateResponse, ApiKeyResponse

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


@router.post("", response_model=ApiKeyCreateResponse)
def create_api_key(
    req: ApiKeyCreateRequest,
    _auth=Depends(require_scope()),
):
    """
    Create a new API key.

    **Requires session token auth** (API keys cannot create other API keys).
    The plaintext key is returned exactly once and cannot be recovered.
    """
    try:
        plaintext, stored = api_keys.create_key(
            name=req.name,
            scopes=req.scopes,
            expires_at=req.expires_at,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ApiKeyCreateResponse(
        id=stored["id"],
        name=stored["name"],
        key=plaintext,
        scopes=stored["scopes"],
        created_at=stored["created_at"],
        expires_at=stored["expires_at"],
    )


@router.get("", response_model=list[ApiKeyResponse])
def list_api_keys(_auth=Depends(require_scope())):
    """
    List all API keys (no key hashes exposed).

    **Requires session token auth**.
    """
    keys = api_keys.list_keys()
    return [ApiKeyResponse(**k) for k in keys]


@router.delete("/{key_id}")
def revoke_api_key(key_id: str, _auth=Depends(require_scope())):
    """
    Revoke an API key.

    **Requires session token auth**.
    """
    if not api_keys.revoke_key(key_id):
        raise HTTPException(status_code=404, detail=f"API key '{key_id}' not found")
    return {"ok": True}
