"""Verify README's quickstart block mirrors examples/quickstart.py."""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path

README_PATH = Path("README.md")
EXAMPLE_PATH = Path("examples/quickstart.py")
BEGIN_MARKER = "<!-- example:quickstart.py:begin -->"
END_MARKER = "<!-- example:quickstart.py:end -->"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true", help="rewrite the README block")
    args = parser.parse_args()

    readme = README_PATH.read_text(encoding="utf-8")
    example = _normalize(EXAMPLE_PATH.read_text(encoding="utf-8"))
    rendered = _render_block(example)

    start, end = _marker_span(readme)
    current_section = readme[start:end]
    current_example = _extract_fenced_code(current_section)

    if _normalize(current_example) == example:
        return 0

    if args.fix:
        README_PATH.write_text(readme[:start] + rendered + readme[end:], encoding="utf-8")
        return 0

    diff = difflib.unified_diff(
        current_example.splitlines(keepends=True),
        example.splitlines(keepends=True),
        fromfile="README.md quickstart block",
        tofile=str(EXAMPLE_PATH),
    )
    print("README quickstart example is out of sync. Run:")
    print("  python scripts/check_readme_example.py --fix")
    print("".join(diff))
    return 1


def _marker_span(readme: str) -> tuple[int, int]:
    try:
        start = readme.index(BEGIN_MARKER)
        end = readme.index(END_MARKER, start) + len(END_MARKER)
    except ValueError as exc:
        raise RuntimeError("README quickstart markers are missing") from exc
    return start, end


def _extract_fenced_code(section: str) -> str:
    lines = section.splitlines()
    try:
        fence_start = lines.index("```python")
        fence_end = len(lines) - 1 - list(reversed(lines)).index("```")
    except ValueError as exc:
        raise RuntimeError("README quickstart markers must wrap a python fenced block") from exc
    if fence_end <= fence_start:
        raise RuntimeError("README quickstart fenced block is malformed")
    return "\n".join(lines[fence_start + 1 : fence_end]) + "\n"


def _render_block(example: str) -> str:
    return f"{BEGIN_MARKER}\n```python\n{example}```\n{END_MARKER}"


def _normalize(text: str) -> str:
    lines = [line.rstrip() for line in text.strip().splitlines()]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
