"""Jinja2 template engine for prompt rendering."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from jinja2 import FileSystemLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)
_FRONTMATTER_PATTERN = re.compile(
    r"\A---[ \t]*\r?\n.*?^---[ \t]*(?:\r?\n|$)",
    re.DOTALL | re.MULTILINE,
)


def create_jinja_env(template_dir: Path) -> SandboxedEnvironment:
    """Create a sandboxed Jinja2 environment rooted at the template directory.

    Uses SandboxedEnvironment for security (prevents template code from
    accessing arbitrary Python objects). StrictUndefined raises errors on
    missing variables rather than silently rendering empty strings.
    """
    return SandboxedEnvironment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_template(template_path: Path, variables: dict[str, Any]) -> str:
    """Load and render a Jinja2 template file with variable substitution.

    Args:
        template_path: Path to the template file (.md or .j2).
        variables: Dict of variable names to values for substitution.

    Returns:
        The rendered template string.
    """
    environment = create_jinja_env(template_path.parent)
    template = environment.get_template(template_path.name)
    return template.render(**variables)


def render_string(template_str: str, variables: dict[str, Any]) -> str:
    """Render a template from a string (not a file).

    Args:
        template_str: Jinja2 template as a string.
        variables: Dict of variable names to values for substitution.

    Returns:
        The rendered string.
    """
    env = SandboxedEnvironment(
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    template = env.from_string(template_str)
    return template.render(**variables)


def resolve_template_path(
    configured_path: Path | None,
    base_dir: Path,
    fallback_name: str | None = None,
) -> Path | None:
    """Resolve a configured template path, warning if missing.

    Args:
        configured_path: User-configured path (may be relative).
        base_dir: Base directory for resolving relative paths.
        fallback_name: Optional name for log messages.

    Returns:
        Resolved absolute Path, or None if not found.
    """
    if configured_path is None:
        return None

    resolved = configured_path
    if not resolved.is_absolute():
        resolved = (base_dir / resolved).resolve()

    if resolved.exists():
        return resolved

    logger.warning(
        "Template not found: '%s'%s. Falling back to built-in.",
        configured_path,
        f" (configured for {fallback_name})" if fallback_name else "",
    )
    return None


def strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter from template content.

    Frontmatter is delimited by ``---`` on its own line at the start
    of the file, followed by another ``---`` line that must also start
    at the beginning of a line.

    Args:
        content: Raw template content potentially containing frontmatter.

    Returns:
        Content with frontmatter removed.
    """
    return _FRONTMATTER_PATTERN.sub("", content)
