"""Retry policies with partial failure semantics."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class FailureMode(StrEnum):
    """How to handle failures in multi-agent phases."""

    FAIL_FAST = "fail_fast"
    """Stop phase on first agent failure."""

    CONTINUE = "continue"
    """Run all agents, report failures in PhaseResult."""

    REQUIRE_MINIMUM = "require_minimum"
    """Require at least min_count agents to succeed."""


class RetryPolicy(BaseModel):
    """Configurable retry policy for agent executions.

    Controls retry behavior on failure, including backoff schedule,
    maximum attempts, and partial failure semantics for parallel phases.
    """

    max_retries: int = 3
    backoff_schedule: list[int] = Field(default_factory=lambda: [120, 300, 900, 1800])
    max_wait: int = 21600  # 6 hours
    failure_mode: FailureMode = FailureMode.FAIL_FAST
    min_count: int = 1  # For REQUIRE_MINIMUM mode

    def should_retry(self, attempt: int) -> bool:
        """Check if another retry attempt is allowed.

        Args:
            attempt: Current attempt number (1-based).

        Returns:
            True if attempt is within max_retries limit.
        """
        return attempt <= self.max_retries

    def evaluate_results(
        self,
        succeeded: int,
        failed: int,
        total: int,
    ) -> Literal["done", "partial", "failed"]:
        """Evaluate the outcome of a parallel phase execution.

        Args:
            succeeded: Number of agents that succeeded.
            failed: Number of agents that failed.
            total: Total number of agents in the phase.

        Returns:
            "done" if success criteria met, "partial" if some succeeded,
            "failed" if criteria not met.
        """
        _ = total

        if self.failure_mode == FailureMode.FAIL_FAST:
            return "done" if failed == 0 else "failed"

        if self.failure_mode == FailureMode.CONTINUE:
            if failed == 0:
                return "done"
            if succeeded > 0:
                return "partial"
            return "failed"

        if self.failure_mode == FailureMode.REQUIRE_MINIMUM:
            if succeeded >= self.min_count:
                return "done" if failed == 0 else "partial"
            return "failed"

        return "failed"
