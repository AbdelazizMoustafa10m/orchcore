"""Colored ANSI logging -- standalone, no Rich dependency."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
DIM = "\033[2m"
BOLD = "\033[1m"
NC = "\033[0m"

ICON_INFO = ">"
ICON_SUCCESS = "+"
ICON_WARN = "!"
ICON_ERROR = "x"
ICON_TIMER = "#"
ICON_COST = "$"


def _timestamp() -> str:
    """Return current time as HH:MM:SS."""
    return datetime.now(UTC).strftime("%H:%M:%S")


def log_info(msg: str) -> None:
    """Log an info message (cyan, arrow icon)."""
    print(f"{DIM}{_timestamp()}{NC} {CYAN}{ICON_INFO}{NC} {msg}", file=sys.stderr)


def log_success(msg: str) -> None:
    """Log a success message (green, checkmark icon)."""
    print(f"{DIM}{_timestamp()}{NC} {GREEN}{ICON_SUCCESS}{NC} {msg}", file=sys.stderr)


def log_warn(msg: str) -> None:
    """Log a warning message (yellow, warning icon)."""
    print(f"{DIM}{_timestamp()}{NC} {YELLOW}{ICON_WARN}{NC} {msg}", file=sys.stderr)


def log_error(msg: str) -> None:
    """Log an error message (red, cross icon)."""
    print(f"{DIM}{_timestamp()}{NC} {RED}{ICON_ERROR}{NC} {msg}", file=sys.stderr)


def log_dim(msg: str) -> None:
    """Log a dim/muted message (gray, no icon)."""
    print(f"{DIM}{_timestamp()} {msg}{NC}", file=sys.stderr)


def status_line(
    elapsed: float,
    tool_count: int,
    cost: float,
    state: str,
) -> None:
    """Write an overwriting status line showing current progress.

    Uses \r to overwrite the previous line for compact real-time display.
    """
    minutes = int(elapsed) // 60
    seconds = int(elapsed) % 60
    time_str = f"{minutes}m{seconds:02d}s" if minutes > 0 else f"{seconds}s"
    line = (
        f"\r{DIM}{ICON_TIMER} {time_str}{NC}"
        f" | {CYAN}{tool_count} tools{NC}"
        f" | {GREEN}{ICON_COST}{cost:.2f}{NC}"
        f" | {MAGENTA}{state}{NC}"
    )
    print(line, end="", file=sys.stderr, flush=True)


def clear_status_line() -> None:
    """Clear the status line."""
    print("\r" + (" " * 80) + "\r", end="", file=sys.stderr, flush=True)


def phase_header(name: str, index: int, total: int) -> None:
    """Render a phase header as a section divider."""
    header = "=" * 60
    label = f"  Phase {index + 1}/{total}: {name}  "
    print(f"\n{BOLD}{CYAN}{header}{NC}", file=sys.stderr)
    print(f"{BOLD}{CYAN}{label}{NC}", file=sys.stderr)
    print(f"{BOLD}{CYAN}{header}{NC}\n", file=sys.stderr)


def summary_box(title: str, items: dict[str, str]) -> None:
    """Render a summary box with key-value pairs."""
    width = 50
    print(f"\n{BOLD}{'=' * width}{NC}", file=sys.stderr)
    print(f"{BOLD}  {title}{NC}", file=sys.stderr)
    print(f"{BOLD}{'-' * width}{NC}", file=sys.stderr)
    for key, value in items.items():
        print(f"  {key}: {CYAN}{value}{NC}", file=sys.stderr)
    print(f"{BOLD}{'=' * width}{NC}\n", file=sys.stderr)
