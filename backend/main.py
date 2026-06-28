"""
spud-router v2.3.0 — FastAPI application entry point.

Mounts all routers and serves the built frontend from /opt/spud-router/static/.

Run with:
    sudo /opt/spud-router/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080
"""
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .routers import auth, config, firewall, network, tailscale, update, wireless

app = FastAPI(
    title="spud-router",
    version="2.3.0",
    docs_url="/api/docs",
    redoc_url=None,
)

# Restrict CORS to localhost only — this is a LAN-only admin interface.
# Set SPUD_EXTRA_ORIGIN to allow access from a custom hostname/IP without
# wildcard, e.g. SPUD_EXTRA_ORIGIN=http://spud-router.lan:8080
_ALLOWED_ORIGINS = [
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]
_extra = os.environ.get("SPUD_EXTRA_ORIGIN", "").strip()
if _extra:
    _ALLOWED_ORIGINS.append(_extra)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Session-Token"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(network.router)
app.include_router(firewall.router)
app.include_router(tailscale.router)
app.include_router(wireless.router)
app.include_router(config.router)
app.include_router(update.router)

# ── Static file serving ───────────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent.parent / "static"

@app.get("/", include_in_schema=False)
def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse(
        {"status": "spud-router running", "ui": "index.html not found in static/"},
        status_code=200,
    )

if STATIC_DIR.exists():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
