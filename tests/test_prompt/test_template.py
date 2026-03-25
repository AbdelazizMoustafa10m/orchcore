from __future__ import annotations

import logging
from pathlib import Path

import pytest
from jinja2 import FileSystemLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from orchcore.prompt import template as template_module
from orchcore.prompt.template import (
    create_jinja_env,
    render_string,
    render_template,
    resolve_template_path,
    strip_frontmatter,
)


def test_create_jinja_env_returns_sandboxed_environment(tmp_path: Path) -> None:
    env = create_jinja_env(tmp_path)

    assert isinstance(env, SandboxedEnvironment)
    assert isinstance(env.loader, FileSystemLoader)
    assert env.loader.searchpath == [str(tmp_path)]
    assert env.undefined is StrictUndefined
    assert env.keep_trailing_newline is True
    assert env.trim_blocks is True
    assert env.lstrip_blocks is True


def test_render_template_renders_template_file(tmp_path: Path) -> None:
    template_path = tmp_path / "greeting.md"
    template_path.write_text("Hello {{ name }}!\n", encoding="utf-8")

    rendered = render_template(template_path, {"name": "Ada"})

    assert rendered == "Hello Ada!\n"


def test_render_string_renders_template_string() -> None:
    rendered = render_string("Value: {{ value }}", {"value": 42})

    assert rendered == "Value: 42"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            "---\n"
            "title: Example\n"
            "---\n"
            "Body text\n",
            "Body text\n",
        ),
        (
            "Body text\n"
            "---\n"
            "not frontmatter\n",
            "Body text\n---\nnot frontmatter\n",
        ),
    ],
)
def test_strip_frontmatter_removes_only_leading_frontmatter(
    content: str,
    expected: str,
) -> None:
    assert strip_frontmatter(content) == expected


def test_resolve_template_path_returns_resolved_existing_relative_path(
    tmp_path: Path,
) -> None:
    template_path = tmp_path / "templates" / "planner.md"
    template_path.parent.mkdir()
    template_path.write_text("template", encoding="utf-8")

    resolved = resolve_template_path(Path("templates/planner.md"), tmp_path)

    assert resolved == template_path.resolve()


def test_resolve_template_path_returns_none_when_path_not_configured() -> None:
    assert resolve_template_path(None, Path.cwd()) is None


def test_resolve_template_path_logs_warning_for_missing_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=template_module.logger.name):
        resolved = resolve_template_path(
            Path("templates/missing.md"),
            tmp_path,
            fallback_name="planner",
        )

    assert resolved is None
    assert "Template not found: 'templates/missing.md'" in caplog.text
    assert "(configured for planner)" in caplog.text
