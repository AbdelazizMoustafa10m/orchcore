from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic_settings import SettingsError

from orchcore.config import BaseSettings as PackageBaseSettings
from orchcore.config import settings as settings_module
from orchcore.config.settings import (
    BaseSettings as ModuleBaseSettings,
)
from orchcore.config.settings import (
    OrchcoreSettings,
    load_settings_with_profile,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_base_settings_alias_is_exported_from_module_and_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    module_settings = ModuleBaseSettings()
    package_settings = PackageBaseSettings()

    assert ModuleBaseSettings is OrchcoreSettings
    assert PackageBaseSettings is OrchcoreSettings
    assert isinstance(module_settings, OrchcoreSettings)
    assert isinstance(package_settings, OrchcoreSettings)


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


def test_orchcore_settings_load_dotenv_from_current_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ORCHCORE_CONCURRENCY", raising=False)
    monkeypatch.delenv("ORCHCORE_LOG_LEVEL", raising=False)
    (tmp_path / ".env").write_text(
        "ORCHCORE_CONCURRENCY=9\nORCHCORE_LOG_LEVEL=trace\n",
        encoding="utf-8",
    )

    settings = OrchcoreSettings()

    assert settings.concurrency == 9
    assert settings.log_level == "trace"


def test_orchcore_settings_load_agent_tables_from_cwd_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "orchcore.toml").write_text(
        "[agents.claude]\n"
        'model = "claude-custom"\n'
        "stall_timeout = 400\n"
        "\n"
        "[agents.claude.env_vars]\n"
        'HTTP_PROXY = "http://proxy.internal"\n',
        encoding="utf-8",
    )

    settings = OrchcoreSettings()

    assert settings.agents == {
        "claude": {
            "model": "claude-custom",
            "stall_timeout": 400,
            "env_vars": {"HTTP_PROXY": "http://proxy.internal"},
        }
    }


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
        "concurrency = 11\n"
        'log_level = "warning"\n'
        "[tool.orchcore.profiles.dev]\n"
        "concurrency = 13\n"
        'workspace_dir = ".dev-workspace"\n',
        encoding="utf-8",
    )

    settings = load_settings_with_profile(profile="dev")

    assert settings.concurrency == 13
    assert settings.log_level == "warning"
    assert settings.workspace_dir == ".dev-workspace"
    assert settings.profile == "dev"


def test_profile_source_get_field_value_reports_no_direct_field_value(
    tmp_path: Path,
) -> None:
    source = settings_module._ProfileTomlSettingsSource(
        OrchcoreSettings,
        config_path=tmp_path / "missing.toml",
        profile_name="dev",
        table_path=("profiles",),
    )

    assert source.get_field_value(None, "concurrency") == (None, "concurrency", False)
    assert source() == {}


def test_load_settings_with_profile_rejects_non_table_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "orchcore.toml").write_text(
        '[profiles]\ndev = "not-a-table"\n',
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match=r"Expected \[profiles.dev\]"):
        load_settings_with_profile(profile="dev")


def test_load_toml_file_wraps_decode_errors(tmp_path: Path) -> None:
    config_path = tmp_path / "broken.toml"
    config_path.write_text("[broken\n", encoding="utf-8")

    with pytest.raises(SettingsError, match="Failed to parse TOML"):
        settings_module._load_toml_file(config_path)


def test_load_toml_table_rejects_non_table_parent(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match=r"Expected \[profiles\]"):
        settings_module._load_toml_table(
            {"profiles": "not-a-table"},
            table_path=("profiles", "dev"),
            config_path=tmp_path / "orchcore.toml",
        )


def test_load_toml_table_rejects_non_table_leaf(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match=r"Expected \[profiles.dev\]"):
        settings_module._load_toml_table(
            {"profiles": {"dev": "not-a-table"}},
            table_path=("profiles", "dev"),
            config_path=tmp_path / "orchcore.toml",
        )
