"""Remote syslog forwarding tab."""
from ..api import GET, POST, PUT
from ..ui import (
    bold, dim, err, hi, ok, warn,
    clear, confirm, menu, pause, print_logo,
    print_status_bar, prompt, section, table,
)

PROTOCOLS = ("udp", "tcp", "tls")
FACILITIES = (
    "*", "kern", "user", "mail", "daemon", "auth", "syslog", "lpr", "news",
    "uucp", "cron", "authpriv", "ftp",
    "local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7",
)
SEVERITIES = ("*", "emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")


def screen(state: dict) -> None:
    while True:
        clear()
        print_logo()
        print_status_bar(state)
        section("Remote Syslog")

        try:
            cfg = GET("/api/syslog")
        except RuntimeError as e:
            print(err(f"  Error: {e}"))
            pause()
            return

        enabled = cfg.get("enabled", False)
        table(["Setting", "Value"], [
            ["Forwarding", ok("enabled") if enabled else dim("disabled")],
            ["Server",     f"{cfg.get('server','—')}:{cfg.get('port',514)}"],
            ["Protocol",   cfg.get("protocol", "udp")],
            ["Selector",   f"{cfg.get('facility','*')}.{cfg.get('severity','*')}"],
            ["Keep local", ok("yes") if cfg.get("keep_local", True) else dim("no")],
        ])

        idx = menu("Syslog Actions", [
            ("Edit settings",    ""),
            ("Test connection",  ""),
            ("Reload",           ""),
        ])
        if idx == -1:
            return
        if idx == 0:
            _edit(cfg)
        elif idx == 1:
            _test(cfg)
        state = GET("/api/state")


def _edit(cfg: dict) -> None:
    section("Edit Syslog Settings")
    try:
        enabled  = confirm("Enable remote forwarding?")
        server   = prompt("Server (host or IP)", cfg.get("server", ""))
        port     = int(prompt("Port", str(cfg.get("port", 514))))
        print(dim(f"  Protocols: {', '.join(PROTOCOLS)}"))
        protocol = prompt("Protocol", cfg.get("protocol", "udp"))
        print(dim(f"  Facilities: {', '.join(FACILITIES)}"))
        facility = prompt("Facility", cfg.get("facility", "*"))
        print(dim(f"  Severities: {', '.join(SEVERITIES)}"))
        severity = prompt("Severity", cfg.get("severity", "*"))
        keep_local = confirm("Keep logging locally too?")
    except (ValueError, KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        PUT("/api/syslog", {
            "enabled": enabled, "server": server, "port": port,
            "protocol": protocol, "facility": facility, "severity": severity,
            "keep_local": keep_local,
        })
        print(ok("\n  ✓ Syslog settings saved"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _test(cfg: dict) -> None:
    section("Test Syslog Connection")
    if not cfg.get("server"):
        print(err("  No server configured — edit settings first."))
        pause()
        return
    try:
        result = POST("/api/syslog/test", cfg)
        if result.get("reachable"):
            print(ok(f"\n  ✓ {result.get('message')}"))
        else:
            print(warn(f"\n  ⚠ {result.get('message')}"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()
