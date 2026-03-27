"""Agent registry -- central lookup for agent CLI configurations."""

from __future__ import annotations

import logging
import shutil
import tomllib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from orchcore.registry.agent import AgentConfig, AgentMode, OutputExtraction, StreamFormat

logger = logging.getLogger(__name__)


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

    def load_from_toml(self, path: Path) -> None:
        """Load agent configurations from a TOML file.

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

        agents_data = data.get("agents", {})
        for name, agent_data in agents_data.items():
            if not isinstance(agent_data, dict):
                logger.warning(
                    "Skipping malformed agent entry %r in %s: expected a TOML table, got %s",
                    name,
                    path,
                    type(agent_data).__name__,
                )
                continue
            agent_data["name"] = name
            # Parse nested models
            if "output_extraction" in agent_data:
                agent_data["output_extraction"] = OutputExtraction(
                    **agent_data["output_extraction"]
                )
            if "stream_format" in agent_data:
                agent_data["stream_format"] = StreamFormat(agent_data["stream_format"])
            if "flags" in agent_data:
                agent_data["flags"] = {AgentMode(k): v for k, v in agent_data["flags"].items()}
            config = AgentConfig(**agent_data)
            self.register(config)

    def with_overrides(self, overrides: dict[str, dict[str, Any]]) -> AgentRegistry:
        """Return a new registry with per-agent field overrides applied.

        Args:
            overrides: dict mapping agent names to dicts of field updates.
                       e.g. {"claude": {"model": "claude-opus-4-6"}}
        """
        patched: dict[str, AgentConfig] = {}
        for name, config in self._agents.items():
            override = overrides.get(name)
            if override is None:
                patched[name] = config
                continue
            # Merge env_vars if both exist
            if "env_vars" in override and config.env_vars:
                override = {**override, "env_vars": {**config.env_vars, **override["env_vars"]}}
            patched[name] = AgentConfig.model_validate({**config.model_dump(), **override})
        return AgentRegistry(patched)


__all__ = ["AgentRegistry"]
