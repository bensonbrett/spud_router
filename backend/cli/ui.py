"""
CLI display primitives for spud-cli.

All terminal I/O lives here — colours, menus, tables, prompts, the logo.
Tabs import from this module; nothing else should print directly.
"""
import re
import shutil
import sys
from pathlib import Path

def _get_version() -> str:
    """Read version from VERSION file."""
    try:
        return Path("/opt/spud-router/VERSION").read_text().strip()
    except Exception:
        return "unknown"

VERSION = _get_version()

# ── ANSI colours ──────────────────────────────────────────────────────────────
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    GREY   = "\033[90m"


def _c(color: str, text: str) -> str:
    return f"{color}{text}{C.RESET}"


def bold(t: str)  -> str: return _c(C.BOLD, t)
def dim(t: str)   -> str: return _c(C.DIM + C.GREY, t)
def ok(t: str)    -> str: return _c(C.GREEN, t)
def warn(t: str)  -> str: return _c(C.YELLOW, t)
def err(t: str)   -> str: return _c(C.RED, t)
def hi(t: str)    -> str: return _c(C.CYAN, t)
def accent(t: str)-> str: return _c(C.BLUE + C.BOLD, t)


def _strip_ansi(text: str) -> str:
    """Return text with ANSI escape codes removed (for width calculations)."""
    return re.sub(r'\033\[[0-9;]*m', '', text)


# ── Terminal helpers ──────────────────────────────────────────────────────────
def tw() -> int:
    """Current terminal width, capped at 80."""
    return min(shutil.get_terminal_size((80, 24)).columns, 80)


def clear() -> None:
    print("\033[2J\033[H", end="")


def hr(char: str = "─") -> None:
    print(_c(C.GREY, char * tw()))


def section(title: str) -> None:
    w = tw()
    print()
    print(_c(C.BLUE, "┌" + "─" * (w - 2) + "┐"))
    pad   = (w - 2 - len(title)) // 2
    inner = " " * pad + bold(title) + " " * (w - 2 - pad - len(title))
    print(_c(C.BLUE, "│") + inner + _c(C.BLUE, "│"))
    print(_c(C.BLUE, "└" + "─" * (w - 2) + "┘"))


def pause(msg: str = "Press Enter to continue...") -> None:
    try:
        input(_c(C.GREY, f"\n  {msg}"))
    except (KeyboardInterrupt, EOFError):
        pass


def prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {_c(C.CYAN, '›')} {msg}{suffix} ").strip()
        return val if val else default
    except (KeyboardInterrupt, EOFError):
        return default


def confirm(msg: str) -> bool:
    return prompt(f"{msg} [y/N]").lower() == "y"


def multiline_prompt(msg: str, terminator: str = "END") -> str:
    """
    Read a multi-line paste (PEM certs/keys, WireGuard configs, etc.) from
    the terminal: prints the prompt, then reads lines until one contains
    only `terminator`, or two consecutive blank lines (some terminals eat
    the exact terminator line on paste). Returns the joined text with a
    trailing newline, or "" if cancelled.
    """
    print(f"  {_c(C.CYAN, '›')} {msg}")
    print(dim(f"    (paste, then a line with just '{terminator}' to finish)"))
    lines: list[str] = []
    blank_run = 0
    try:
        while True:
            line = input()
            if line.strip() == terminator:
                break
            if line.strip() == "":
                blank_run += 1
                if blank_run >= 2 and lines:
                    break
            else:
                blank_run = 0
            lines.append(line)
    except (KeyboardInterrupt, EOFError):
        return ""
    return "\n".join(lines).strip() + "\n" if lines else ""


def menu(title: str, options: list[tuple[str, str]], back_label: str = "Back") -> int:
    """
    Display a numbered menu and return the selected 0-based index.
    Returns -1 if the user selects 0 / back / q / Enter.
    """
    while True:
        print()
        print(f"  {bold(title)}")
        hr()
        for i, (label, desc) in enumerate(options, 1):
            num      = _c(C.CYAN, f"  {i:2}.")
            desc_str = f"  {dim(desc)}" if desc else ""
            print(f"{num} {bold(label)}{desc_str}")
        print(f"{_c(C.GREY, '   0.')} {dim(back_label)}")
        hr()
        choice = prompt("Select")
        if choice in ("0", "q", ""):
            return -1
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return idx
            print(err(f"  Enter 1–{len(options)} or 0"))
        except ValueError:
            print(err("  Enter a number"))


def table(headers: list[str], rows: list[list]) -> None:
    """Print a plain aligned table. Handles ANSI codes in cells correctly."""
    if not rows:
        print(dim("  (none)"))
        return

    # Calculate column widths ignoring ANSI escape codes
    widths = [len(h) for h in headers]
    str_rows = [[str(cell) for cell in row] for row in rows]
    for row in str_rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(_strip_ansi(cell)))

    header_fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    print(_c(C.GREY, header_fmt.format(*headers)))
    print(_c(C.GREY, "  " + "  ".join("─" * w for w in widths)))

    for row in str_rows:
        parts = []
        for i, cell in enumerate(row):
            plain   = _strip_ansi(cell)
            padding = " " * max(0, (widths[i] if i < len(widths) else 0) - len(plain))
            parts.append(cell + padding)
        print("  " + "  ".join(parts))


# ── ASCII logo ────────────────────────────────────────────────────────────────
_LOGO = [
    r"   ___ _ __  _   _  __| |      _ __ ___  _   _| |_ ___ _ __ ",
    r"  / __| '_ \| | | |/ _` |_____| '__/ _ \| | | | __/ _ \ '__|",
    r"  \__ \ |_) | |_| | (_| |_____| | | (_) | |_| | ||  __/ |   ",
    r"  |___/ .__/ \__,_|\__,_|     |_|  \___/ \__,_|\__\___|_|   ",
    r"      |_|                                                     ",
]


def print_logo() -> None:
    for line in _LOGO:
        print(_c(C.BLUE, line))
    tag = f"  spud-router v{VERSION}  ·  router-on-a-stick"
    print(_c(C.GREY, tag))
    print()


def print_status_bar(state: dict) -> None:
    """One-line status summary shown at the top of every screen."""
    r          = state.get("router", {})
    vlans      = state.get("vlans", [])
    ts         = state.get("tailscale", {})
    ts_str     = ok("TS:on") if ts.get("enabled") else dim("TS:off")
    print(
        f"  {bold(r.get('hostname', 'spud-router'))}  "
        f"{dim('wan:')} {hi(r.get('wan_interface', '?'))} {dim(r.get('wan_mode', ''))}  "
        f"{dim('vlans:')} {hi(str(len(vlans)))}  "
        f"{ts_str}"
        f"{_pending_changes_segment()}"
    )


def _pending_changes_segment() -> str:
    """
    '  ⚠ Unapplied changes' when state.json has edits not yet pushed live
    via Apply, else ''. Best-effort — a backend error here shouldn't take
    down every screen's status bar, so failures are silently swallowed.
    """
    from . import api  # deferred: avoids a circular import at module load time
    try:
        if api.GET("/api/apply/status").get("pending"):
            return f"  {warn('⚠ Unapplied changes')}"
    except RuntimeError:
        pass
    return ""
    hr()
