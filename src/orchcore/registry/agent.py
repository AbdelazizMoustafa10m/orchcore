"""Agent configuration models for the orchcore registry."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from orchcore.stream.events import StreamFormat

DEFAULT_TOOLSET_PERMISSION = "read-only"
DEFAULT_TOOLSET_MAX_TURNS = 25
CODEX_PERMISSION_VALUES = frozenset({"read-only", "workspace-write", "full-access"})


class AgentMode(StrEnum):
    """Agent execution modes."""

    PLAN = "plan"
    FIX = "fix"
    AUDIT = "audit"
    REVIEW = "review"


class OutputExtraction(BaseModel):
    """Defines how to extract final text output from an agent's raw stream."""

    class Strategy(StrEnum):
        JQ_FILTER = "jq_filter"
        DIRECT_FILE = "direct_file"
        STDOUT_CAPTURE = "stdout_capture"

    strategy: Strategy
    # Declarative reference for the extraction logic applied by StreamParser.
    # The stream parser extracts text natively (no jq binary required); this
    # field documents the equivalent jq expression for human reference and
    # future tooling that may invoke jq directly.
    jq_expression: str | None = None
    strip_preamble: bool = False
    stderr_as_stream: bool = False


class AgentConfig(BaseModel):
    """Single agent definition."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    binary: str
    model: str
    subcommand: str
    flags: dict[AgentMode, list[str]]
    stream_format: StreamFormat
    env_vars: dict[str, str] = Field(default_factory=dict)
    output_extraction: OutputExtraction
    stall_timeout: float = 300.0
    deep_tool_timeout: float = 600.0


class ToolSet(BaseModel):
    """Tools available for a specific execution context.

    Defines which tools an agent is allowed to use within a phase.
    Internal tools are native to the agent CLI (e.g., Read, Write, Edit).
    MCP tools reference external tool servers (e.g., Tavily, Exa).
    Permission level controls the agent's write access.
    Max turns limits conversation depth for the agent invocation.

    ToolSet is resolved per agent per phase using the resolution order:
        Phase.agent_tools[agent] > explicit_toolset > Phase.tools > AgentConfig.flags[mode]
        > defaults
    """

    internal: list[str] = Field(default_factory=list)
    mcp: list[str] = Field(default_factory=list)
    permission: str = DEFAULT_TOOLSET_PERMISSION
    max_turns: int = DEFAULT_TOOLSET_MAX_TURNS


__all__ = [
    "AgentConfig",
    "AgentMode",
    "OutputExtraction",
    "StreamFormat",
    "ToolSet",
]
