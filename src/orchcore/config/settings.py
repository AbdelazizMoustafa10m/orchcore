"""Layered configuration system with TOML support and profiles."""

from __future__ import annotations

import tomllib
from pathlib import Path
from types import new_class
from typing import Any, cast

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    PyprojectTomlConfigSettingsSource,
    SettingsConfigDict,
    SettingsError,
    TomlConfigSettingsSource,
)

type SettingsData = dict[str, Any]
type TomlTablePath = tuple[str, ...]

_PROJECT_TOML_PATH = Path("orchcore.toml")
_USER_TOML_PATH = Path.home() / ".config" / "orchcore" / "config.toml"
_PYPROJECT_TOML_PATH = Path("pyproject.toml")
_PYPROJECT_TABLE_HEADER: tuple[str, str] = ("tool", "orchcore")


class _ProfileTomlSettingsSource(PydanticBaseSettingsSource):
    """Read a named profile section from a TOML configuration file."""

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        *,
        config_path: Path,
        profile_name: str,
        table_path: TomlTablePath,
    ) -> None:
        super().__init__(settings_cls)
        self._profile_data = _load_profile_data(
            config_path=config_path,
            profile_name=profile_name,
            table_path=table_path,
        )

    def get_field_value(
        self,
        field: Any,
        field_name: str,
    ) -> tuple[Any, str, bool]:
        del field
        return None, field_name, False

    def __call__(self) -> SettingsData:
        return self._profile_data


class OrchcoreSettings(BaseSettings):
    """Base settings for orchcore."""

    concurrency: int = Field(default=4, ge=1)
    stall_timeout: int = Field(default=300, ge=1)
    deep_tool_timeout: int = Field(default=600, ge=1)

    workspace_dir: str = ".orchcore-workspace"
    reports_dir: str = "reports"

    max_retries: int = Field(default=3, ge=0)
    max_wait: int = Field(default=21600, ge=1)

    log_level: str = "info"
    profile: str | None = None

    agents: dict[str, SettingsData] = Field(default_factory=dict)

    model_config = SettingsConfigDict(
        env_prefix="ORCHCORE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        pyproject_toml_table_header=_PYPROJECT_TABLE_HEADER,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del cls, file_secret_settings
        return _build_settings_sources(
            settings_cls=settings_cls,
            init_settings=init_settings,
            env_settings=env_settings,
            dotenv_settings=dotenv_settings,
        )


def load_settings_with_profile(
    settings_class: type[OrchcoreSettings] = OrchcoreSettings,
    profile: str | None = None,
    **overrides: Any,
) -> OrchcoreSettings:
    """Load settings with an optional named profile overlay."""

    settings = settings_class(**overrides)
    effective_profile = profile or settings.profile
    if effective_profile is None:
        return settings

    profiled_settings_class = _profiled_settings_class(settings_class, effective_profile)
    profiled_overrides = dict(overrides)
    profiled_overrides["profile"] = effective_profile
    return profiled_settings_class(**profiled_overrides)


def _profiled_settings_class(
    settings_class: type[OrchcoreSettings],
    profile_name: str,
) -> type[OrchcoreSettings]:
    def settings_customise_sources(
        cls: type[BaseSettings],
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del cls, file_secret_settings
        return _build_settings_sources(
            settings_cls=settings_cls,
            init_settings=init_settings,
            env_settings=env_settings,
            dotenv_settings=dotenv_settings,
            profile_name=profile_name,
        )

    def exec_body(namespace: dict[str, Any]) -> None:
        namespace["__module__"] = settings_class.__module__
        namespace["settings_customise_sources"] = classmethod(settings_customise_sources)

    profiled_settings_class = new_class(
        f"{settings_class.__name__}ProfiledSettings",
        (settings_class,),
        exec_body=exec_body,
    )
    return cast("type[OrchcoreSettings]", profiled_settings_class)


def _build_settings_sources(
    *,
    settings_cls: type[BaseSettings],
    init_settings: PydanticBaseSettingsSource,
    env_settings: PydanticBaseSettingsSource,
    dotenv_settings: PydanticBaseSettingsSource,
    profile_name: str | None = None,
) -> tuple[PydanticBaseSettingsSource, ...]:
    sources: list[PydanticBaseSettingsSource] = [
        init_settings,
        env_settings,
        dotenv_settings,
    ]

    if profile_name is not None:
        sources.extend(
            [
                _ProfileTomlSettingsSource(
                    settings_cls,
                    config_path=_PROJECT_TOML_PATH,
                    profile_name=profile_name,
                    table_path=("profiles",),
                ),
                _ProfileTomlSettingsSource(
                    settings_cls,
                    config_path=_USER_TOML_PATH,
                    profile_name=profile_name,
                    table_path=("profiles",),
                ),
                _ProfileTomlSettingsSource(
                    settings_cls,
                    config_path=_PYPROJECT_TOML_PATH,
                    profile_name=profile_name,
                    table_path=(*_PYPROJECT_TABLE_HEADER, "profiles"),
                ),
            ]
        )

    sources.extend(
        [
            TomlConfigSettingsSource(settings_cls, toml_file=_PROJECT_TOML_PATH),
            TomlConfigSettingsSource(settings_cls, toml_file=_USER_TOML_PATH),
            PyprojectTomlConfigSettingsSource(settings_cls, toml_file=_PYPROJECT_TOML_PATH),
        ]
    )
    return tuple(sources)


def _load_profile_data(
    *,
    config_path: Path,
    profile_name: str,
    table_path: TomlTablePath,
) -> SettingsData:
    if not config_path.is_file():
        return {}

    toml_data = _load_toml_file(config_path)
    profiles = _load_toml_table(toml_data, table_path=table_path, config_path=config_path)
    if not profiles:
        return {}

    profile_data = profiles.get(profile_name)
    if profile_data is None:
        return {}
    if not isinstance(profile_data, dict):
        table_name = ".".join((*table_path, profile_name))
        raise SettingsError(f"Expected [{table_name}] in {config_path} to be a TOML table")
    return dict(profile_data)


def _load_toml_file(config_path: Path) -> SettingsData:
    try:
        with config_path.open("rb") as file_obj:
            return tomllib.load(file_obj)
    except OSError as exc:
        raise SettingsError(f"Failed to read configuration file {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"Failed to parse TOML configuration file {config_path}") from exc


def _load_toml_table(
    toml_data: SettingsData,
    *,
    table_path: TomlTablePath,
    config_path: Path,
) -> SettingsData:
    current: Any = toml_data
    traversed_path: list[str] = []

    for key in table_path:
        traversed_path.append(key)
        if not isinstance(current, dict):
            table_name = ".".join(traversed_path[:-1])
            raise SettingsError(f"Expected [{table_name}] in {config_path} to be a TOML table")
        if key not in current:
            return {}
        current = current[key]

    if not isinstance(current, dict):
        table_name = ".".join(traversed_path)
        raise SettingsError(f"Expected [{table_name}] in {config_path} to be a TOML table")
    return dict(current)
