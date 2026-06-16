"""Live status tab — interfaces, routing table, DHCP leases."""
from ..api import GET
from ..ui import (
    bold, dim, hi, ok, warn,
    clear, pause, print_logo, section, table,
)


def screen() -> None:
    clear()
    print_logo()
    section("Live Status")

    try:
        status = GET("/api/status")
    except RuntimeError as e:
        print(f"  Error fetching status: {e}")
        pause()
        return

    # Interfaces
    print(f"  {bold('Interfaces')}")
    for line in status.get("interfaces", "").strip().split("\n"):
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            state_col = ok if parts[1] == "UP" else warn
            print(
                f"    {hi(f'{parts[0]:<16}')}"
                f" {state_col(f'{parts[1]:<10}')}"
                f" {dim(' '.join(parts[2:]))}"
            )

    # Routing table
    print(f"\n  {bold('Routing Table')}")
    for line in status.get("routes", "").strip().split("\n"):
        if line:
            print(f"    {dim(line)}")

    # DHCP leases
    leases = status.get("leases", [])
    print(f"\n  {bold(f'DHCP Leases ({len(leases)})')}")
    if leases:
        table(
            ["IP", "MAC", "Hostname"],
            [[l["ip"], l["mac"], l["hostname"]] for l in leases],
        )
    else:
        print(dim("  No active leases."))

    pause()
