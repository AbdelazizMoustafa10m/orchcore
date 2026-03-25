"""Phase and pipeline result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from orchcore.registry.agent import ToolSet  # noqa: TC001
from orchcore.stream.events import AgentResult  # noqa: TC001

if TYPE_CHECKING:
    from datetime import timedelta
    from decimal import Decimal


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

    name: str
    agents: list[str]
    parallel: bool = False
    required: bool = True
    depends_on: list[str] = Field(default_factory=list)
    tools: ToolSet | None = None
    agent_tools: dict[str, ToolSet] = Field(default_factory=dict)


@dataclass
class PhaseResult:
    """Result of a single pipeline phase."""

    name: str
    status: PhaseStatus
    duration: timedelta | None = None
    output_files: list[Path] = field(default_factory=list)
    agent_results: list[AgentResult] = field(default_factory=list)
    error: str | None = None
    cost_usd: Decimal | None = None


@dataclass
class PipelineResult:
    """Aggregated result of a complete pipeline run."""

    phases: list[PhaseResult]
    total_duration: timedelta
    total_cost_usd: Decimal | None
    success: bool


__all__ = ["Phase", "PhaseResult", "PhaseStatus", "PipelineResult"]
