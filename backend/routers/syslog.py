"""
Remote syslog forwarding config: GET/PUT /api/syslog, POST /api/syslog/test.

The connectivity test is pure Python socket/ssl — no shell, no subprocess —
against a server/port that's already gone through SyslogConfig validation.
"""
import socket
import ssl

from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..models import SyslogConfig
from ..state import load_state, save_state

router = APIRouter(tags=["syslog"], dependencies=[Depends(require_auth)])

TEST_TIMEOUT_SECONDS = 5


@router.get("/api/syslog")
def get_syslog():
    return load_state().get("syslog", SyslogConfig().model_dump())


@router.put("/api/syslog")
def set_syslog(config: SyslogConfig):
    state = load_state()
    state["syslog"] = config.model_dump()
    save_state(state)
    return {"ok": True}


@router.post("/api/syslog/test")
def test_syslog(config: SyslogConfig):
    """Attempt to reach server:port over the configured protocol."""
    if not config.server:
        return {"reachable": False, "message": "No server configured"}

    try:
        if config.protocol == "udp":
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(TEST_TIMEOUT_SECONDS)
                # UDP is connectionless — this just confirms the hostname
                # resolves and the OS will hand off the datagram; it cannot
                # confirm anything is actually listening on the far end.
                sock.sendto(b"", (config.server, config.port))
            return {"reachable": True, "message": f"UDP datagram sent to {config.server}:{config.port}"}

        with socket.create_connection((config.server, config.port), timeout=TEST_TIMEOUT_SECONDS) as sock:
            if config.protocol == "tls":
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(sock, server_hostname=config.server):
                    pass
            return {"reachable": True, "message": f"Connected to {config.server}:{config.port} ({config.protocol})"}

    except (socket.timeout, socket.gaierror, ConnectionRefusedError, OSError, ssl.SSLError) as e:
        return {"reachable": False, "message": str(e)}
