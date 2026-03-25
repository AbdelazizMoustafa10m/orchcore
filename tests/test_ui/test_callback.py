from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Protocol, runtime_checkable

from orchcore.pipeline import Phase, PhaseResult, PhaseStatus, PipelineResult
from orchcore.stream.events import AgentResult, StreamEvent, StreamEventType
from orchcore.ui.callback import LoggingCallback, NullCallback, UICallback


@runtime_checkable
class _RuntimeUICallback(UICallback, Protocol):
    pass


def test_null_callback_methods_are_callable() -> None:
    callback = NullCallback()
    phase = Phase(name="phase", agents=["agent"])
    phase_result = PhaseResult(name="phase", status=PhaseStatus.DONE)
    pipeline_result = PipelineResult(
        phases=[phase_result],
        total_duration=timedelta(seconds=1),
        total_cost_usd=Decimal("0"),
        success=True,
    )
    stream_event = StreamEvent(event_type=StreamEventType.INIT)
    agent_result = AgentResult(agent_name="agent")

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
    assert isinstance(NullCallback(), _RuntimeUICallback)


def test_logging_callback_logs_messages(caplog) -> None:
    callback = LoggingCallback()
    phase = Phase(name="phase", agents=["agent"])
    phase_result = PhaseResult(name="phase", status=PhaseStatus.DONE)
    pipeline_result = PipelineResult(
        phases=[phase_result],
        total_duration=timedelta(seconds=2),
        total_cost_usd=None,
        success=True,
    )
    agent_result = AgentResult(agent_name="agent", exit_code=0)

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

    assert "Pipeline starting with 1 phases" in caplog.text
    assert "Pipeline complete: success=True" in caplog.text
    assert "Phase 'phase' starting" in caplog.text
    assert "Phase 'phase' ended: done" in caplog.text
    assert "Agent 'agent' starting in phase 'phase'" in caplog.text
    assert "Agent 'agent' complete: exit_code=0" in caplog.text
    assert "Agent 'agent' error: failure" in caplog.text
    assert "Agent 'agent' rate limited: wait" in caplog.text
    assert "Shutdown: done" in caplog.text
