"""Smoke test an installed orchcore distribution artifact.

This script is intentionally stdlib-only. CI runs it from the repository
checkout while installing a built wheel or sdist into an isolated uv
environment, so the guard below fails if the import accidentally resolves to
``src/orchcore`` instead of the installed distribution.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.resources
import tempfile
from pathlib import Path

MODULES = (
    "orchcore",
    "orchcore.config",
    "orchcore.display",
    "orchcore.observability",
    "orchcore.pipeline",
    "orchcore.prompt",
    "orchcore.recovery",
    "orchcore.registry",
    "orchcore.runner",
    "orchcore.signals",
    "orchcore.stream",
    "orchcore.ui",
    "orchcore.workspace",
)


def main() -> None:
    """Import the installed distribution and exercise a dry-run agent path."""
    orchcore = importlib.import_module("orchcore")
    _ensure_installed_distribution(orchcore)

    for module_name in MODULES:
        importlib.import_module(module_name)

    version = getattr(orchcore, "__version__", "")
    _check(version not in {"", "0.0.0"}, f"bad installed version: {version!r}")
    _check(
        importlib.resources.files("orchcore").joinpath("py.typed").is_file(),
        "py.typed missing from installed distribution",
    )

    from orchcore.registry import AgentRegistry
    from orchcore.runner import AgentRunner

    with tempfile.TemporaryDirectory(prefix="orchcore-smoke-") as tmp:
        tmp_path = Path(tmp)
        registry_path = tmp_path / "agents.toml"
        registry_path.write_text(
            "\n".join(
                [
                    "[agents.fake]",
                    'binary = "fake-agent"',
                    'model = "fake-model"',
                    'subcommand = "run"',
                    'stream_format = "claude"',
                    "flags = { plan = [] }",
                    'output_extraction = { strategy = "stdout_capture" }',
                    "",
                ]
            ),
            encoding="utf-8",
        )

        registry = AgentRegistry()
        registry.load_from_toml(registry_path)
        _check(registry.list_agents() == ["fake"], "registry load smoke failed")

        result = asyncio.run(
            AgentRunner().run(
                agent=registry.get("fake"),
                prompt="Smoke prompt",
                output_path=tmp_path / "out.md",
                flag_profile="plan",
                dry_run=True,
            )
        )
        _check(result.exit_code == 0, f"dry-run agent failed: {result!r}")

    print(f"smoke OK {version}")


def _ensure_installed_distribution(module: object) -> None:
    module_file = getattr(module, "__file__", None)
    _check(isinstance(module_file, str), "orchcore module has no __file__")
    imported_path = Path(module_file).resolve()
    repo_source = Path(__file__).resolve().parents[1] / "src" / "orchcore"
    if imported_path.is_relative_to(repo_source.resolve()):
        msg = f"smoke test imported source tree instead of installed dist: {imported_path}"
        raise RuntimeError(msg)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


if __name__ == "__main__":
    main()
