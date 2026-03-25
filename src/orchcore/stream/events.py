from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal  # noqa: TC003 — required by Pydantic runtime validation
from enum import StrEnum
from pathlib import Path  # noqa: TC003 — required by Pydantic runtime validation
from typing import Literal

from pydantic import BaseModel, Field


class StreamFormat(StrEnum):
    """JSONL stream format variants."""

    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"
    GEMINI = "gemini"
    COPILOT = "copilot"


class StreamEventType(StrEnum):
    """Rich event taxonomy covering the full agent lifecycle."""

    INIT = "init"
    STATE_CHANGE = "state"
    TOOL_START = "tool_start"
    TOOL_EXEC = "tool_exec"
    TOOL_DONE = "tool_done"
    TEXT = "text"
    SUBAGENT = "subagent"
    RESULT = "result"
    RETRY = "retry"
    RATE_LIMIT = "rate_limit"
    STALL = "stall"


class StreamEvent(BaseModel):
    """Normalized event from any agent stream."""

    event_type: StreamEventType
    timestamp: datetime = Field(default_factory=datetime.now)

    # Tool lifecycle fields (TOOL_START, TOOL_EXEC, TOOL_DONE)
    tool_id: str | None = None
    tool_name: str | None = None
    tool_detail: str | None = None
    tool_status: Literal["running", "done", "error"] | None = None
    tool_duration_ms: int | None = None

    # Text fields
    text_preview: str | None = None

    # Result fields (RESULT)
    cost_usd: Decimal | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    session_id: str | None = None
    token_usage: dict[str, int] | None = None

    # Retry/rate limit/stall fields
    retry_attempt: int | None = None
    retry_max: int | None = None
    retry_delay_ms: int | None = None
    error_category: str | None = None
    idle_seconds: float | None = None

    # Raw event for debugging
    raw: dict[str, object] | None = None


class AgentState(StrEnum):
    """Agent state machine."""

    STARTING = "starting"
    THINKING = "thinking"
    WRITING = "writing"
    TOOL_ACTIVE = "tool_active"
    IDLE = "idle"
    STALLED = "stalled"
    RATE_LIMITED = "rate_limited"
    COMPLETED = "completed"
    FAILED = "failed"


class ToolExecution(BaseModel):
    """Tracks a single tool invocation through its lifecycle."""

    tool_id: str
    name: str
    friendly_name: str
    detail: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    status: Literal["running", "done", "error"] = "running"
    duration: timedelta | None = None


class ToolCounters(BaseModel):
    """Aggregated tool execution statistics."""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    running: int = 0


class AgentMonitorSnapshot(BaseModel):
    """Point-in-time state of a monitored agent."""

    agent_name: str
    state: AgentState
    elapsed: timedelta
    counters: ToolCounters
    active_tools: list[ToolExecution]
    recent_tools: list[ToolExecution]
    last_tool: str | None = None
    last_tool_detail: str | None = None
    cost_usd: Decimal | None = None
    token_usage: dict[str, int] | None = None
    text_count: int = 0
    subagent_count: int = 0
    session_id: str | None = None
    num_turns: int | None = None
    idle_seconds: float = 0.0


class AgentResult(BaseModel):
    """Return type of AgentRunner.run(). Captures all outputs from a single agent execution."""

    agent_name: str = ""
    output_path: Path | None = None
    stream_path: Path | None = None
    log_path: Path | None = None
    exit_code: int = 0
    duration: timedelta | None = None
    cost_usd: Decimal | None = None
    token_usage: dict[str, int] | None = None
    num_turns: int | None = None
    session_id: str | None = None
    output_empty: bool = False
    error: str | None = None


__all__ = [
    "AgentMonitorSnapshot",
    "AgentResult",
    "AgentState",
    "StreamEvent",
    "StreamEventType",
    "StreamFormat",
    "ToolCounters",
    "ToolExecution",
]
