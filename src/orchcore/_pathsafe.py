"""Path containment helpers shared by workspace and prompt loading code."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath


def _is_absolute_or_drive_path(path: Path, raw_name: str) -> bool:
    """Return True for native absolute paths and Windows drive paths on every OS."""
    windows_path = PureWindowsPath(raw_name)
    return path.is_absolute() or windows_path.is_absolute() or bool(windows_path.drive)


def resolve_within(base: Path, name: str) -> Path:
    """Join ``name`` onto ``base``, rejecting absolute names and escapes."""
    candidate = Path(name)
    if _is_absolute_or_drive_path(candidate, name):
        raise ValueError(f"absolute paths are not allowed: {name!r}")

    base_resolved = base.resolve()
    resolved = (base / candidate).resolve()
    if resolved != base_resolved and not resolved.is_relative_to(base_resolved):
        raise ValueError(f"path {name!r} escapes {base_resolved}")
    return resolved
