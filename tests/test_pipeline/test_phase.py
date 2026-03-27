from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from orchcore.pipeline import Phase, PhaseResult, PhaseStatus, PipelineResult
from orchcore.recovery.retry import FailureMode, RetryPolicy
from orchcore.registry.agent import ToolSet
from orchcore.stream.events import AgentResult


def test_phase_status_has_all_expected_values() -> None:
    assert [status.value for status in PhaseStatus] == [
        "pending",
        "running",
        "done",
        "skipped",
        "failed",
        "partial",
    ]


def test_phase_creation_uses_defaults() -> None:
    # Arrange
    phase = Phase(name="planning", agents=["writer", "reviewer"])

    # Act
    dumped = phase.model_dump(mode="json")

    # Assert
    assert phase.name == "planning"
    assert phase.parallel is False
    assert phase.required is True
    assert phase.depends_on == []
    assert phase.tools is None
    assert phase.agent_tools == {}
    assert phase.retry_policy is None
    assert phase.failure_mode is FailureMode.FAIL_FAST
    assert dumped["retry_policy"] is None
    assert dumped["failure_mode"] == "fail_fast"


def test_phase_model_dump_serializes_retry_policy_and_failure_mode() -> None:
    # Arrange
    retry_policy = RetryPolicy(
        max_retries=5,
        backoff_schedule=[30, 90],
        max_wait=300,
        failure_mode=FailureMode.REQUIRE_MINIMUM,
        min_count=2,
    )
    phase = Phase(
        name="analysis",
        agents=["analyst", "reviewer"],
        retry_policy=retry_policy,
        failure_mode=FailureMode.CONTINUE,
    )

    # Act
    dumped = phase.model_dump(mode="json")
    restored = Phase.model_validate(dumped)

    # Assert
    assert dumped["retry_policy"] == {
        "max_retries": 5,
        "backoff_schedule": [30, 90],
        "max_wait": 300,
        "failure_mode": "require_minimum",
        "min_count": 2,
    }
    assert dumped["failure_mode"] == "continue"
    assert restored == phase


def test_phase_creation_with_tools_and_agent_tools() -> None:
    tools = ToolSet(internal=["Read"], mcp=["exa"], permission="full-access", max_turns=4)
    phase = Phase(
        name="analysis",
        agents=["analyst"],
        tools=tools,
        agent_tools={"analyst": ToolSet(internal=["Edit"])},
    )

    assert phase.tools == tools
    assert phase.agent_tools["analyst"].internal == ["Edit"]


def test_phase_result_creation() -> None:
    result = PhaseResult(
        name="analysis",
        status=PhaseStatus.DONE,
        duration=timedelta(minutes=3),
        output_files=[Path("report.md")],
        agent_results=[AgentResult(agent_name="analyst")],
        error=None,
        cost_usd=Decimal("1.50"),
    )

    assert result.name == "analysis"
    assert result.status is PhaseStatus.DONE
    assert result.output_files == [Path("report.md")]
    assert result.agent_results[0].agent_name == "analyst"
    assert result.cost_usd == Decimal("1.50")


def test_pipeline_result_creation() -> None:
    phase_result = PhaseResult(name="analysis", status=PhaseStatus.DONE)
    pipeline_result = PipelineResult(
        phases=[phase_result],
        total_duration=timedelta(minutes=5),
        total_cost_usd=Decimal("2.50"),
        success=True,
    )

    assert pipeline_result.phases == [phase_result]
    assert pipeline_result.total_duration == timedelta(minutes=5)
    assert pipeline_result.total_cost_usd == Decimal("2.50")
    assert pipeline_result.success is True
