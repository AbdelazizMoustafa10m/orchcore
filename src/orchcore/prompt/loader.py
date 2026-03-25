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

    Supports .md and .j2 file extensions. Strips YAML frontmatter
    from loaded templates.
    """

    EXTENSIONS: tuple[str, ...] = (".md", ".j2", ".txt")

    def __init__(self, template_dirs: list[Path]) -> None:
        self._dirs = template_dirs

    def load(self, template_name: str) -> str:
        """Load a template by name, searching all configured directories.

        Args:
            template_name: Template filename (with or without extension).

        Returns:
            Template content with frontmatter stripped.

        Raises:
            FileNotFoundError: If the template is not found in any directory.
        """
        for dir_path in self._dirs:
            candidate = dir_path / template_name
            if candidate.exists():
                return strip_frontmatter(candidate.read_text(encoding="utf-8"))

            for ext in self.EXTENSIONS:
                candidate = dir_path / f"{template_name}{ext}"
                if candidate.exists():
                    return strip_frontmatter(candidate.read_text(encoding="utf-8"))

        searched = ", ".join(str(dir_path) for dir_path in self._dirs)
        msg = f"Template '{template_name}' not found in: {searched}"
        raise FileNotFoundError(msg)

    def exists(self, template_name: str) -> bool:
        """Check if a template exists in any configured directory."""
        for dir_path in self._dirs:
            candidate = dir_path / template_name
            if candidate.exists():
                return True

            for ext in self.EXTENSIONS:
                if (dir_path / f"{template_name}{ext}").exists():
                    return True

        return False
