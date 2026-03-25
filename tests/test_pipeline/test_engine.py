from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from orchcore.pipeline.engine import PhaseRunner, _failed_phase, _skipped_phase
from orchcore.pipeline.phase import PhaseResult, PhaseStatus
from orchcore.registry.registry import AgentRegistry
from orchcore.runner.subprocess import AgentRunner

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize(
    ("builder", "expected_status"),
    [
        pytest.param(_failed_phase, PhaseStatus.FAILED, id="failed-phase"),
        pytest.param(_skipped_phase, PhaseStatus.SKIPPED, id="skipped-phase"),
    ],
)
def test_phase_result_helpers_build_expected_results(
    builder: Callable[[str, str], PhaseResult],
    expected_status: PhaseStatus,
) -> None:
    # Arrange
    name = "analysis"
    reason = "phase was skipped"

    # Act
    result = builder(name, reason)

    # Assert
    assert result.name == name
    assert result.status is expected_status
    assert result.duration is None
    assert result.output_files == []
    assert result.agent_results == []
    assert result.error == reason
    assert result.cost_usd is None


def test_phase_runner_constructor_accepts_required_parameters() -> None:
    # Arrange
    runner = AgentRunner()
    registry = AgentRegistry()

    # Act
    phase_runner = PhaseRunner(runner=runner, registry=registry)

    # Assert
    assert phase_runner._runner is runner
    assert phase_runner._registry is registry
    assert phase_runner._workspace is None
