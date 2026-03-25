from __future__ import annotations

from typing import TYPE_CHECKING

from orchcore.config.settings import OrchcoreSettings, load_settings_with_profile

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_orchcore_settings_use_defaults_in_empty_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    settings = OrchcoreSettings()

    assert settings.concurrency == 4
    assert settings.stall_timeout == 300
    assert settings.deep_tool_timeout == 600
    assert settings.workspace_dir == ".orchcore-workspace"
    assert settings.reports_dir == "reports"
    assert settings.max_retries == 3
    assert settings.max_wait == 21600
    assert settings.log_level == "info"
    assert settings.profile is None
    assert settings.agents == {}


def test_orchcore_settings_allow_environment_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ORCHCORE_CONCURRENCY", "12")
    monkeypatch.setenv("ORCHCORE_LOG_LEVEL", "debug")
    monkeypatch.setenv("ORCHCORE_MAX_RETRIES", "5")

    settings = OrchcoreSettings()

    assert settings.concurrency == 12
    assert settings.log_level == "debug"
    assert settings.max_retries == 5


def test_load_settings_with_profile_without_profile_reads_cwd_toml_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "orchcore.toml").write_text("concurrency = 7\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.orchcore]\nlog_level = "warning"\nconcurrency = 11\n',
        encoding="utf-8",
    )

    settings = load_settings_with_profile()

    assert settings.concurrency == 7
    assert settings.log_level == "warning"
    assert settings.profile is None


def test_load_settings_with_profile_reads_profile_from_cwd_pyproject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.orchcore]\n"
        'concurrency = 11\n'
        'log_level = "warning"\n'
        '[tool.orchcore.profiles.dev]\n'
        "concurrency = 13\n"
        'workspace_dir = ".dev-workspace"\n',
        encoding="utf-8",
    )

    settings = load_settings_with_profile(profile="dev")

    assert settings.concurrency == 13
    assert settings.log_level == "warning"
    assert settings.workspace_dir == ".dev-workspace"
    assert settings.profile == "dev"
