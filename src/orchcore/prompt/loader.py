"""Template loader with frontmatter stripping and directory search."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from orchcore.prompt.template import strip_frontmatter

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class TemplateLoader:
    """Loads prompt templates from configurable directories.

    Supports .md, .j2, .jinja2, and .txt file extensions. Strips YAML
    frontmatter from loaded templates.
    """

    EXTENSIONS: tuple[str, ...] = (".md", ".j2", ".txt")

    def __init__(self, template_dirs: list[Path]) -> None:
        self._dirs = template_dirs

    def _resolve_template_path(self, template_name: str) -> Path | None:
        """Return the first matching template path, or None if not found.

        Searches each configured directory in order. For each directory,
        tries the name as-is first, then appends each known extension.
        """
        for dir_path in self._dirs:
            candidate = dir_path / template_name
            if candidate.exists():
                return candidate

            for ext in self.EXTENSIONS:
                candidate = dir_path / f"{template_name}{ext}"
                if candidate.exists():
                    return candidate

        return None

    def load(self, template_name: str) -> str:
        """Load a template by name, searching all configured directories.

        Args:
            template_name: Template filename (with or without extension).

        Returns:
            Template content with frontmatter stripped.

        Raises:
            FileNotFoundError: If the template is not found in any directory.
        """
        path = self._resolve_template_path(template_name)
        if path is not None:
            return strip_frontmatter(path.read_text(encoding="utf-8"))

        searched = ", ".join(str(dir_path) for dir_path in self._dirs)
        msg = f"Template '{template_name}' not found in: {searched}"
        raise FileNotFoundError(msg)

    def exists(self, template_name: str) -> bool:
        """Check if a template exists in any configured directory."""
        return self._resolve_template_path(template_name) is not None
