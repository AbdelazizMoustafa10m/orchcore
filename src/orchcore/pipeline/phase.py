"""Phase and pipeline result models."""

from __future__ import annotations

from datetime import timedelta  # noqa: TC003 — required by Pydantic runtime validation
from decimal import Decimal  # noqa: TC003 — required by Pydantic runtime validation
from enum import StrEnum
from pathlib import Path  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field

from orchcore.recovery.retry import FailureMode, RetryPolicy
from orchcore.registry.agent import ToolSet  # noqa: TC001
from orchcore.stream.events import AgentResult  # noqa: TC001


class PhaseStatus(StrEnum):
    """Status of a pipeline phase."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    PARTIAL = "partial"


class Phase(BaseModel):
    """Definition of a pipeline phase."""

    model_config = ConfigDict(frozen=True)

    name: str
    agents: list[str]
    parallel: bool = False
    required: bool = True
    depends_on: list[str] = Field(default_factory=list)
    tools: ToolSet | None = None
    agent_tools: dict[str, ToolSet] = Field(default_factory=dict)
    retry_policy: RetryPolicy | None = Field(default=None)
    failure_mode: FailureMode = Field(default=FailureMode.FAIL_FAST)


class PhaseResult(BaseModel):
    """Result of a single pipeline phase."""

    name: str
    status: PhaseStatus
    duration: timedelta | None = None
    output_files: list[Path] = Field(default_factory=list)
    agent_results: list[AgentResult] = Field(default_factory=list)
    error: str | None = None
    cost_usd: Decimal | None = None


class PipelineResult(BaseModel):
    """Aggregated result of a complete pipeline run."""

    phases: list[PhaseResult]
    total_duration: timedelta
    total_cost_usd: Decimal | None
    success: bool


__all__ = ["Phase", "PhaseResult", "PhaseStatus", "PipelineResult"]
