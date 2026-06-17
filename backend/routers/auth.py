"""Auth routes: login, logout, change-password."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..auth import (
    _load_credentials,
    _verify_password_hash,
    create_token,
    require_auth,
    revoke_token,
    update_password,
    verify_credentials,
)
from ..models import ChangePasswordRequest, LoginRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])

TOKEN_TTL = 8 * 3600


@router.post("/login")
def login(req: LoginRequest):
    if not verify_credentials(req.username, req.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_token()
    resp  = JSONResponse({"ok": True, "token": token})
    resp.set_cookie(
        "spud_token",
        token,
        httponly=True,
        samesite="strict",
        max_age=TOKEN_TTL,
    )
    return resp


@router.post("/logout")
def logout(request: Request):
    token = (
        request.headers.get("X-Session-Token")
        or request.cookies.get("spud_token")
    )
    if token:
        revoke_token(token)
    return {"ok": True}


@router.post("/change-password", dependencies=[Depends(require_auth)])
def change_password(req: ChangePasswordRequest):
    _, stored_hash = _load_credentials()
    if not _verify_password_hash(req.current_password, stored_hash):
        raise HTTPException(status_code=400, detail="Current password incorrect")

    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    update_password(req.new_password)
    return {"ok": True}
