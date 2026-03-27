"""Formatting utilities for duration, cost, file size, and tokens."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import timedelta
    from decimal import Decimal


def format_duration(td: timedelta | None) -> str:
    """Format a timedelta as ``Xm Ys`` (e.g. ``"2m 30s"``).

    Returns a dash when *td* is ``None``. Sub-minute durations render as
    ``"Xs"`` only.
    """
    if td is None:
        return "\u2014"
    total = int(td.total_seconds())
    if total < 0:
        return "\u2014"
    minutes, seconds = divmod(total, 60)
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def format_cost(cost: Decimal | None) -> str:
    """Format a cost as ``"$X.XXXX"`` or ``"N/A"`` when *cost* is ``None``."""
    if cost is None:
        return "N/A"
    return f"${cost:.4f}"


def format_file_size(size_bytes: int) -> str:
    """Format a file size in bytes as a human-readable string.

    Uses KB (1024 bytes) or MB (1048576 bytes) as appropriate; falls back
    to plain bytes for very small files.
    """
    if size_bytes >= 1_048_576:
        return f"{size_bytes / 1_048_576:.1f} MB"
    if size_bytes >= 1_024:
        return f"{size_bytes / 1_024:.1f} KB"
    return f"{size_bytes} bytes"


def format_tokens(token_usage: Mapping[str, int] | None) -> str:
    """Format token usage as ``"in/out"`` or a dash when unavailable."""
    if token_usage is None:
        return "\u2014"
    in_tokens = token_usage.get("input_tokens", token_usage.get("in", 0))
    out_tokens = token_usage.get("output_tokens", token_usage.get("out", 0))
    return f"{in_tokens:,}/{out_tokens:,}"
