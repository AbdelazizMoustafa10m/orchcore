from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Protocol, runtime_checkable

import pytest

from orchcore.pipeline import Phase, PhaseResult, PhaseStatus, PipelineResult
from orchcore.stream.events import AgentResult, StreamEvent, StreamEventType
from orchcore.ui.callback import LoggingCallback, NullCallback, UICallback


@runtime_checkable
class _RuntimeUICallback(UICallback, Protocol):
    pass


def _build_callback_inputs() -> tuple[Phase, PhaseResult, PipelineResult, StreamEvent, AgentResult]:
    phase = Phase(name="phase", agents=["agent"])
    phase_result = PhaseResult(name="phase", status=PhaseStatus.DONE)
    pipeline_result = PipelineResult(
        phases=[phase_result],
        total_duration=timedelta(seconds=2),
        total_cost_usd=Decimal("0"),
        success=True,
    )
    stream_event = StreamEvent(event_type=StreamEventType.INIT)
    agent_result = AgentResult(agent_name="agent", exit_code=0)
    return phase, phase_result, pipeline_result, stream_event, agent_result


def test_null_callback_methods_are_callable() -> None:
    # Arrange
    callback = NullCallback()
    phase, phase_result, pipeline_result, stream_event, agent_result = _build_callback_inputs()

    # Act
    callback.on_pipeline_start([phase])
    callback.on_pipeline_complete(pipeline_result)
    callback.on_phase_start(phase)
    callback.on_phase_end(phase, phase_result)
    callback.on_phase_skip(phase, "not needed")
    callback.on_agent_start("agent", "phase")
    callback.on_agent_event(stream_event)
    callback.on_agent_complete("agent", agent_result)
    callback.on_agent_error("agent", "failure")
    callback.on_stall_detected("agent", 1.5)
    callback.on_rate_limit("agent", "slow down")
    callback.on_rate_limit_wait("agent", 2.0)
    callback.on_retry("agent", 2, 3)
    callback.on_git_recovery("reset", "working tree restored")
    callback.on_shutdown("finished")


def test_null_callback_satisfies_protocol_structurally() -> None:
    # Act / Assert
    assert isinstance(NullCallback(), _RuntimeUICallback)


def test_logging_callback_logs_core_messages(caplog: pytest.LogCaptureFixture) -> None:
    # Arrange
    callback = LoggingCallback()
    phase, phase_result, pipeline_result, _, agent_result = _build_callback_inputs()

    # Act
    with caplog.at_level(logging.INFO, logger="orchcore.ui"):
        callback.on_pipeline_start([phase])
        callback.on_pipeline_complete(pipeline_result)
        callback.on_phase_start(phase)
        callback.on_phase_end(phase, phase_result)
        callback.on_agent_start("agent", "phase")
        callback.on_agent_complete("agent", agent_result)
        callback.on_agent_error("agent", "failure")
        callback.on_rate_limit("agent", "wait")
        callback.on_shutdown("done")

    # Assert
    assert "Pipeline starting with 1 phases" in caplog.text
    assert "Pipeline complete: success=True" in caplog.text
    assert "Phase 'phase' starting" in caplog.text
    assert "Phase 'phase' ended: done" in caplog.text
    assert "Agent 'agent' starting in phase 'phase'" in caplog.text
    assert "Agent 'agent' complete: exit_code=0" in caplog.text
    assert "Agent 'agent' error: failure" in caplog.text
    assert "Agent 'agent' rate limited: wait" in caplog.text
    assert "Shutdown: done" in caplog.text


@pytest.mark.parametrize(
    ("method_name", "method_args", "expected_level", "expected_message"),
    [
        pytest.param(
            "on_phase_skip",
            (Phase(name="phase", agents=["agent"]), "not needed"),
            logging.INFO,
            "Phase 'phase' skipped: not needed",
            id="phase-skip-info",
        ),
        pytest.param(
            "on_agent_event",
            (StreamEvent(event_type=StreamEventType.INIT),),
            logging.DEBUG,
            "Agent event: init",
            id="agent-event-debug",
        ),
        pytest.param(
            "on_stall_detected",
            ("agent", 1.5),
            logging.WARNING,
            "Agent 'agent' stalled for 1.5 seconds",
            id="stall-warning",
        ),
        pytest.param(
            "on_rate_limit_wait",
            ("agent", 2.0),
            logging.WARNING,
            "Agent 'agent' waiting 2.0 seconds after rate limit",
            id="rate-limit-wait-warning",
        ),
        pytest.param(
            "on_retry",
            ("agent", 2, 3),
            logging.WARNING,
            "Retrying agent 'agent' (attempt 2/3)",
            id="retry-warning",
        ),
        pytest.param(
            "on_git_recovery",
            ("reset", "working tree restored"),
            logging.INFO,
            "Git recovery 'reset': working tree restored",
            id="git-recovery-info",
        ),
    ],
)
def test_logging_callback_logs_missing_methods_at_expected_levels(
    method_name: str,
    method_args: tuple[object, ...],
    expected_level: int,
    expected_message: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange
    callback = LoggingCallback()

    # Act
    with caplog.at_level(logging.DEBUG, logger="orchcore.ui"):
        getattr(callback, method_name)(*method_args)

    # Assert
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == expected_level
    assert expected_message in caplog.text
