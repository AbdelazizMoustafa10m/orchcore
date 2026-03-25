"""Base configuration schema definitions."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentOverrideConfig(BaseModel):
    """Per-agent configuration overrides from TOML."""

    model: str | None = None
    stall_timeout: int | None = Field(default=None, ge=1)
    deep_tool_timeout: int | None = Field(default=None, ge=1)
    env: dict[str, str] = Field(default_factory=dict)
