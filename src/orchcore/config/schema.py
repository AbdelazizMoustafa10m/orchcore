"""Base configuration schema definitions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AgentOverrideConfig(BaseModel):
    """Per-agent configuration overrides from TOML."""

    model: str | None = None
    stall_timeout: int | None = Field(default=None, ge=1)
    deep_tool_timeout: int | None = Field(default=None, ge=1)
    max_runtime: float | None = Field(default=None, gt=0)
    kill_on_stall: bool | None = None
    env_policy: Literal["inherit", "filtered", "clean"] | None = None
    env_passlist: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
