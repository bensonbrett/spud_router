"""SNMP agent (Net-SNMP, v2c) configuration tab."""
from ..api import GET, PUT
from ..ui import (
    bold, dim, err, hi, ok, warn,
    clear, confirm, menu, pause, print_logo,
    print_status_bar, prompt, section, table,
)

MASKED = "********"


def screen(state: dict) -> None:
    while True:
        clear()
        print_logo()
        print_status_bar(state)
        section("SNMP Agent")

        try:
            cfg = GET("/api/snmp")
        except RuntimeError as e:
            print(err(f"  Error: {e}"))
            pause()
            return

        enabled = cfg.get("enabled", False)
        table(["Setting", "Value"], [
            ["Agent",              ok("enabled") if enabled else dim("disabled")],
            ["RO community",       MASKED if cfg.get("community_ro") else dim("not set")],
            ["RW community",       MASKED if cfg.get("community_rw") else dim("none")],
            ["Bind interface",     cfg.get("bind_interface") or "all"],
            ["Allowlist",          ", ".join(cfg.get("allowlist", [])) or warn("any source")],
            ["Location",           cfg.get("location", "")],
            ["Contact",            cfg.get("contact", "")],
        ])

        idx = menu("SNMP Actions", [
            ("Edit settings", ""),
            ("Reload",        ""),
        ])
        if idx == -1:
            return
        if idx == 0:
            _edit(cfg)
        state = GET("/api/state")


def _edit(cfg: dict) -> None:
    section("Edit SNMP Settings")
    try:
        enabled = confirm("Enable SNMP agent?")

        community_ro = prompt(
            "Read-only community (Enter to keep current)",
            MASKED if cfg.get("community_ro") else "",
        )
        community_rw = prompt(
            "Read-write community (Enter to keep current, blank stays disabled)",
            MASKED if cfg.get("community_rw") else "",
        )

        bind_interface = prompt("Bind interface (blank = all)", cfg.get("bind_interface", ""))
        location = prompt("Location (sysLocation)", cfg.get("location", ""))
        contact  = prompt("Contact (sysContact)", cfg.get("contact", ""))

        allowlist = list(cfg.get("allowlist", []))
        if confirm("Edit allowlist?"):
            allowlist = _edit_allowlist(allowlist)
    except (ValueError, KeyboardInterrupt, EOFError):
        print(err("  Cancelled."))
        return

    try:
        PUT("/api/snmp", {
            "enabled": enabled, "version": "v2c",
            "community_ro": community_ro, "community_rw": community_rw,
            "allowlist": allowlist, "bind_interface": bind_interface,
            "location": location, "contact": contact,
        })
        print(ok("\n  ✓ SNMP settings saved"))
    except RuntimeError as e:
        print(err(f"\n  Error: {e}"))
    pause()


def _edit_allowlist(allowlist: list[str]) -> list[str]:
    allowlist = list(allowlist)
    section("SNMP Allowlist")

    while True:
        print()
        if allowlist:
            for i, entry in enumerate(allowlist, 1):
                print(f"  {i}. {hi(entry)}")
        else:
            print(dim("  No allowlist entries — accepting from any source"))
        print(dim("\n  Enter an IP/CIDR to add, a number to remove, or Enter to finish"))

        try:
            val = prompt("").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not val:
            break

        try:
            i = int(val) - 1
            if 0 <= i < len(allowlist):
                removed = allowlist.pop(i)
                print(dim(f"  Removed {removed}"))
            else:
                print(err("  Invalid number"))
        except ValueError:
            if val not in allowlist:
                allowlist.append(val)
                print(ok(f"  Added {val}"))

    return allowlist
