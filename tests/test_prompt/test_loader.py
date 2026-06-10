from __future__ import annotations

from pathlib import Path

import pytest

from orchcore.prompt.loader import TemplateLoader

ABSOLUTE_TEMPLATE_NAME = f"{Path.cwd().anchor}orchcore-secret"


@pytest.fixture
def template_dirs(tmp_path: Path) -> list[Path]:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    return [first_dir, second_dir]


def test_load_searches_directories_and_strips_frontmatter(
    template_dirs: list[Path],
) -> None:
    second_dir = template_dirs[1]
    (second_dir / "welcome.md").write_text(
        "---\ntitle: Greeting\n---\nHello {{ name }}!\n",
        encoding="utf-8",
    )

    loader = TemplateLoader(template_dirs)

    content = loader.load("welcome")

    assert content == "Hello {{ name }}!\n"


def test_load_raises_file_not_found_error_with_searched_directories(
    template_dirs: list[Path],
) -> None:
    loader = TemplateLoader(template_dirs)

    with pytest.raises(FileNotFoundError) as exc_info:
        loader.load("missing-template")

    message = str(exc_info.value)
    assert "Template 'missing-template' not found in:" in message
    assert str(template_dirs[0]) in message
    assert str(template_dirs[1]) in message


@pytest.mark.parametrize("template_name", ["../secret", ABSOLUTE_TEMPLATE_NAME, r"C:\secret"])
def test_load_rejects_templates_outside_configured_directories(
    template_dirs: list[Path],
    template_name: str,
) -> None:
    loader = TemplateLoader(template_dirs)

    with pytest.raises(ValueError):
        loader.load(template_name)


@pytest.mark.parametrize(
    ("template_name", "expected"),
    [
        ("welcome", True),
        ("welcome.md", True),
        ("missing", False),
    ],
)
def test_exists_checks_template_names_with_and_without_extensions(
    tmp_path: Path,
    template_name: str,
    expected: bool,
) -> None:
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "welcome.md").write_text("Hello\n", encoding="utf-8")
    loader = TemplateLoader([template_dir])

    assert loader.exists(template_name) is expected


@pytest.mark.parametrize("template_name", ["../secret", ABSOLUTE_TEMPLATE_NAME, r"C:\secret"])
def test_exists_rejects_templates_outside_configured_directories(
    template_dirs: list[Path],
    template_name: str,
) -> None:
    loader = TemplateLoader(template_dirs)

    with pytest.raises(ValueError):
        loader.exists(template_name)
