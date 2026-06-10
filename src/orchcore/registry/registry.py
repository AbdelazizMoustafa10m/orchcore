"""Agent registry -- central lookup for agent CLI configurations."""

from __future__ import annotations

import logging
import shutil
import tomllib
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from orchcore.config.schema import AgentOverrideConfig

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

from orchcore.registry.agent import AgentConfig, AgentMode, OutputExtraction, StreamFormat

logger: logging.Logger = logging.getLogger(__name__)


class AgentRegistry:
    """Registry of all available agents.

    Agents can be registered programmatically or loaded from TOML configuration files.
    Built-in agents are NOT hardcoded -- consuming projects register their own agents.
    """

    def __init__(self, agents: dict[str, AgentConfig] | None = None) -> None:
        self._agents: dict[str, AgentConfig] = agents if agents is not None else {}

    def get(self, name: str) -> AgentConfig:
        """Get agent config by name. Raises KeyError if not found."""
        if name not in self._agents:
            raise KeyError(f"Agent '{name}' not found in registry")
        return self._agents[name]

    def register(self, config: AgentConfig) -> None:
        """Register an agent configuration."""
        self._agents[config.name] = config

    def list_agents(self) -> list[str]:
        """Return names of all registered agents."""
        return list(self._agents.keys())

    def available(self) -> list[str]:
        """Return names of agents whose binary is on PATH."""
        return [
            name for name, config in self._agents.items() if shutil.which(config.binary) is not None
        ]

    def validate(self, names: list[str]) -> list[str]:
        """Return agent names that are unknown or whose binaries are missing."""
        return [
            name
            for name in names
            if name not in self._agents or shutil.which(self._agents[name].binary) is None
        ]

    def load_from_toml(self, path: Path, *, on_error: Literal["raise", "skip"] = "raise") -> None:
        """Load agent configurations from a TOML file, atomically.

        All entries are parsed and validated *before* any is registered, so
        the registry is never left half-mutated. With ``on_error="raise"``
        (the default) a single ``ValueError`` naming every invalid entry is
        raised and the registry stays untouched. With ``on_error="skip"``
        the valid entries are registered and each invalid entry is logged as
        a warning.

        Values are used literally; no environment-variable interpolation is
        performed (``"${VAR}"`` reaches the subprocess as that literal
        string). Use ``AgentConfig.env_policy``/``env_passlist`` for ambient
        material, or resolve values in your own configuration layer.

        Expected format:
            [agents.claude]
            binary = "claude"
            model = "claude-sonnet-4-20250514"
            subcommand = "-p"
            stream_format = "claude"
            ...
        """
        with path.open("rb") as f:
            data = tomllib.load(f)

        parsed: list[AgentConfig] = []
        errors: dict[str, str] = {}
        for name, agent_data in data.get("agents", {}).items():
            if not isinstance(agent_data, dict):
                errors[name] = f"expected a TOML table, got {type(agent_data).__name__}"
                continue
            try:
                parsed.append(_parse_agent_entry(name, agent_data))
            except (TypeError, ValueError, ValidationError) as exc:
                errors[name] = str(exc)

        if errors and on_error == "raise":
            detail = "; ".join(f"{name}: {message}" for name, message in sorted(errors.items()))
            raise ValueError(f"Invalid agent entries in {path}: {detail}")
        for name, message in errors.items():
            logger.warning("Skipping invalid agent entry %r in %s: %s", name, path, message)
        for config in parsed:
            self.register(config)

    def with_overrides(
        self,
        overrides: Mapping[str, AgentOverrideConfig | dict[str, Any]],
    ) -> AgentRegistry:
        """Return a new registry with per-agent field overrides applied.

        Args:
            overrides: mapping of agent names to field updates — either a
                typed :class:`AgentOverrideConfig` or a plain dict.
                e.g. ``{"claude": AgentOverrideConfig(model="claude-opus-4-6")}``
                or ``{"claude": {"model": "claude-opus-4-6"}}``.
        """
        patched: dict[str, AgentConfig] = {}
        for name, config in self._agents.items():
            raw_override = overrides.get(name)
            if raw_override is None:
                patched[name] = config
                continue
            override = _as_override_patch(raw_override)
            # Merge env_vars if both exist
            if "env_vars" in override and config.env_vars:
                override = {**override, "env_vars": {**config.env_vars, **override["env_vars"]}}
            patched[name] = AgentConfig.model_validate({**config.model_dump(), **override})
        return AgentRegistry(patched)


def _as_override_patch(override: AgentOverrideConfig | dict[str, Any]) -> dict[str, Any]:
    """Normalize a typed or dict override into an AgentConfig field patch."""
    if isinstance(override, AgentOverrideConfig):
        patch = override.model_dump(exclude_none=True, exclude_defaults=True)
        if "env" in patch:
            # Schema field 'env' maps to AgentConfig.env_vars.
            patch["env_vars"] = patch.pop("env")
        return patch
    return dict(override)


def _parse_agent_entry(name: str, agent_data: dict[str, Any]) -> AgentConfig:
    """Validate one ``[agents.<name>]`` TOML table into an AgentConfig."""
    entry: dict[str, Any] = {**agent_data, "name": name}
    # Parse nested models
    if "output_extraction" in entry:
        entry["output_extraction"] = OutputExtraction(
            **_require_toml_table("output_extraction", entry["output_extraction"])
        )
    if "stream_format" in entry:
        entry["stream_format"] = StreamFormat(entry["stream_format"])
    if "flags" in entry:
        flags = _require_toml_table("flags", entry["flags"])
        entry["flags"] = {AgentMode(key): value for key, value in flags.items()}
    return AgentConfig(**entry)


def _require_toml_table(field_name: str, value: object) -> dict[str, Any]:
    """Return a nested TOML table or raise a per-entry parser error."""
    if not isinstance(value, dict):
        msg = f"{field_name} must be a TOML table, got {type(value).__name__}"
        raise TypeError(msg)
    return value


__all__ = ["AgentRegistry"]
