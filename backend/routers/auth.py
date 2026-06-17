"""Auth routes: login, logout, change-password."""
import time
from collections import defaultdict

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

# Per-IP login rate limiting: max 5 attempts per 60-second window
_LOGIN_MAX    = 5
_LOGIN_WINDOW = 60
_login_log: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(ip: str) -> None:
    now      = time.time()
    cutoff   = now - _LOGIN_WINDOW
    attempts = [t for t in _login_log[ip] if t > cutoff]
    _login_log[ip] = attempts
    if len(attempts) >= _LOGIN_MAX:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts — try again later",
            headers={"Retry-After": str(_LOGIN_WINDOW)},
        )
    _login_log[ip].append(now)


@router.post("/login")
def login(req: LoginRequest, request: Request):
    _check_rate_limit(request.client.host if request.client else "unknown")

    if not verify_credentials(req.username, req.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    _login_log.pop(request.client.host if request.client else "unknown", None)
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
