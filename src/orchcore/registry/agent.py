"""Agent configuration models for the orchcore registry."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orchcore.registry.versioning import IncompatibleVersionSpec
from orchcore.stream.events import StreamFormat

DEFAULT_TOOLSET_PERMISSION = "read-only"
DEFAULT_TOOLSET_MAX_TURNS = 25
CODEX_PERMISSION_VALUES = frozenset({"read-only", "workspace-write", "full-access"})


# Flag profile names must not be mistakable for CLI flags (no leading "-")
# and must be single unquoted TOML-key-friendly tokens.
_FLAG_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


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
    # Tuple values: frozen=True is shallow, so nested containers must be
    # immutable themselves (WP-31). Pydantic coerces list inputs from TOML.
    flags: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    """Named flag profiles. Names are consumer-defined workflow vocabulary
    (``plan``/``fix`` in one project, ``research``/``draft`` in another);
    orchcore attaches no meaning to a name beyond looking it up. A profile
    holds behavioral flags (e.g. ``--think``); tool access and permissions
    belong in a :class:`ToolSet`, whose translation is appended after
    profile flags and therefore wins on last-flag-wins CLIs."""
    stream_format: StreamFormat
    env_vars: dict[str, str] = Field(default_factory=dict)
    env_policy: Literal["inherit", "filtered", "clean"] = "filtered"
    env_passlist: list[str] = Field(default_factory=list)
    output_extraction: OutputExtraction
    stall_timeout: float = 300.0
    deep_tool_timeout: float = 600.0
    max_runtime: float | None = None
    kill_on_stall: bool = False
    prompt_via: Literal["argv", "stdin"] = "argv"
    """How the prompt reaches the agent CLI. ``"stdin"`` keeps the prompt out
    of argv (no ARG_MAX/CreateProcess limits, not visible in process listings)
    and writes it to the child's stdin instead."""
    stdin_sentinel: str | None = None
    """Argv element appended in place of the prompt under ``prompt_via="stdin"``
    for CLIs that need a placeholder (e.g. ``"-"`` for ``codex exec -``).
    Keeps CLI quirks in registry data, not code."""
    version_command: tuple[str, ...] = ("--version",)
    """Arguments appended to ``binary`` to print its version. ``()`` disables
    version detection entirely. The check is advisory: it runs once per binary
    path per process, crosses the same subprocess boundary as agent runs
    (filtered env, explicit cwd, no stdin, hard timeout), and never fails or
    slows the run."""
    compatible_versions: tuple[str, ...] = ()
    """Version specifiers (``">=2.1.112,<3"``) the registry declares as known
    good. Empty means no expectations: detected versions log at DEBUG only."""
    incompatible_versions: tuple[IncompatibleVersionSpec, ...] = ()
    """Known-bad version ranges with linked reasons; matching versions log a
    WARNING naming the reason. Takes precedence over ``compatible_versions``."""

    @field_validator("flags")
    @classmethod
    def _validate_flag_profile_names(
        cls, value: dict[str, tuple[str, ...]]
    ) -> dict[str, tuple[str, ...]]:
        """Reject profile names that could be confused with CLI flags."""
        for name in value:
            if not _FLAG_PROFILE_NAME_RE.match(name):
                msg = (
                    f"Invalid flag profile name {name!r}: must start with an "
                    "alphanumeric character and contain only alphanumerics, "
                    "'.', '_', or '-'"
                )
                raise ValueError(msg)
        return value


class ToolSet(BaseModel):
    """Tools available for a specific execution context.

    Defines which tools an agent is allowed to use within a phase.
    Internal tools are native to the agent CLI (e.g., Read, Write, Edit).
    MCP tools reference external tool servers (e.g., Tavily, Exa).
    Permission level controls the agent's write access.
    Max turns limits conversation depth for the agent invocation.

    ToolSet is resolved per agent per phase using the resolution order:
        Phase.agent_tools[agent] > explicit_toolset > Phase.tools > None

    Flag profiles (``AgentConfig.flags``) are independent of ToolSet
    resolution: when a profile is selected its flags are always applied,
    and the ToolSet translation (when one resolves) is appended after them.
    """

    internal: tuple[str, ...] = ()
    mcp: tuple[str, ...] = ()
    permission: str = DEFAULT_TOOLSET_PERMISSION
    max_turns: int = DEFAULT_TOOLSET_MAX_TURNS


__all__ = [
    "AgentConfig",
    "IncompatibleVersionSpec",
    "OutputExtraction",
    "StreamFormat",
    "ToolSet",
]
