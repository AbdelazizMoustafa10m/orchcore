from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from orchcore.pipeline.engine import PhaseRunner
from orchcore.pipeline.phase import Phase, PhaseResult, PhaseStatus
from orchcore.pipeline.pipeline import PipelineRunner
from orchcore.registry.agent import AgentMode, ToolSet
from orchcore.registry.registry import AgentRegistry
from orchcore.runner.subprocess import AgentRunner
from orchcore.stream import (
    AgentMonitor,
    AgentState,
    StallDetector,
    StreamEvent,
    StreamEventType,
    StreamFilter,
    StreamFormat,
    StreamParser,
)
from orchcore.ui.callback import NullCallback

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from orchcore.ui.callback import UICallback


@dataclass(frozen=True)
class PhaseInvocation:
    method: str
    phase_name: str
    prompt: str
    mode: AgentMode
    toolset: ToolSet | None


class MockPhaseRunner(PhaseRunner):
    """Mock phase runner that records invocations and returns preset results."""

    def __init__(self, results: dict[str, PhaseResult]) -> None:
        super().__init__(runner=AgentRunner(), registry=AgentRegistry())
        self._results = results
        self.calls: list[PhaseInvocation] = []

    async def run_phase(
        self,
        phase: Phase,
        prompt: str,
        ui_callback: UICallback,
        mode: AgentMode,
        toolset: ToolSet | None = None,
    ) -> PhaseResult:
        del ui_callback
        return self._record_call(
            method="run_phase",
            phase=phase,
            prompt=prompt,
            mode=mode,
            toolset=toolset,
        )

    async def run_parallel(
        self,
        phase: Phase,
        prompt: str,
        ui_callback: UICallback,
        mode: AgentMode,
        toolset: ToolSet | None = None,
    ) -> PhaseResult:
        del ui_callback
        return self._record_call(
            method="run_parallel",
            phase=phase,
            prompt=prompt,
            mode=mode,
            toolset=toolset,
        )

    def _record_call(
        self,
        *,
        method: str,
        phase: Phase,
        prompt: str,
        mode: AgentMode,
        toolset: ToolSet | None,
    ) -> PhaseResult:
        self.calls.append(
            PhaseInvocation(
                method=method,
                phase_name=phase.name,
                prompt=prompt,
                mode=mode,
                toolset=toolset,
            )
        )
        return self._results[phase.name]


def _phase(
    name: str,
    *,
    agents: list[str] | None = None,
    parallel: bool = False,
    required: bool = True,
    depends_on: list[str] | None = None,
    tools: ToolSet | None = None,
) -> Phase:
    return Phase(
        name=name,
        agents=agents or [f"{name}-agent"],
        parallel=parallel,
        required=required,
        depends_on=depends_on or [],
        tools=tools,
    )


def _phase_result(
    name: str,
    status: PhaseStatus,
    *,
    error: str | None = None,
) -> PhaseResult:
    return PhaseResult(name=name, status=status, error=error)


@pytest.mark.asyncio
async def test_pipeline_runs_all_phases_sequentially() -> None:
    # Arrange
    phases = [_phase("plan"), _phase("implement"), _phase("review")]
    phase_runner = MockPhaseRunner(
        {
            "plan": _phase_result("plan", PhaseStatus.DONE),
            "implement": _phase_result("implement", PhaseStatus.DONE),
            "review": _phase_result("review", PhaseStatus.DONE),
        }
    )
    pipeline_runner = PipelineRunner(phase_runner=phase_runner)

    # Act
    result = await pipeline_runner.run_pipeline(
        phases=phases,
        prompts={phase.name: f"prompt:{phase.name}" for phase in phases},
        ui_callback=NullCallback(),
    )

    # Assert
    assert [phase_result.name for phase_result in result.phases] == [
        "plan",
        "implement",
        "review",
    ]
    assert [phase_result.status for phase_result in result.phases] == [
        PhaseStatus.DONE,
        PhaseStatus.DONE,
        PhaseStatus.DONE,
    ]
    assert [call.phase_name for call in phase_runner.calls] == [
        "plan",
        "implement",
        "review",
    ]
    assert [call.method for call in phase_runner.calls] == [
        "run_phase",
        "run_phase",
        "run_phase",
    ]
    assert result.success


@pytest.mark.asyncio
async def test_pipeline_uses_parallel_runner_for_parallel_phase() -> None:
    # Arrange
    parallel_phase = _phase(
        "review",
        agents=["review-a", "review-b"],
        parallel=True,
    )
    phase_runner = MockPhaseRunner({"review": _phase_result("review", PhaseStatus.DONE)})
    pipeline_runner = PipelineRunner(phase_runner=phase_runner)

    # Act
    await pipeline_runner.run_pipeline(
        phases=[parallel_phase],
        prompts={"review": "parallel review"},
        ui_callback=NullCallback(),
    )

    # Assert
    assert phase_runner.calls == [
        PhaseInvocation(
            method="run_parallel",
            phase_name="review",
            prompt="parallel review",
            mode=AgentMode.PLAN,
            toolset=None,
        )
    ]


@pytest.mark.asyncio
async def test_pipeline_stops_on_required_phase_failure() -> None:
    # Arrange
    phases = [_phase("plan"), _phase("implement"), _phase("review")]
    phase_runner = MockPhaseRunner(
        {
            "plan": _phase_result("plan", PhaseStatus.DONE),
            "implement": _phase_result(
                "implement",
                PhaseStatus.FAILED,
                error="implementation failed",
            ),
            "review": _phase_result("review", PhaseStatus.DONE),
        }
    )
    pipeline_runner = PipelineRunner(phase_runner=phase_runner)

    # Act
    result = await pipeline_runner.run_pipeline(
        phases=phases,
        prompts={phase.name: phase.name for phase in phases},
        ui_callback=NullCallback(),
    )

    # Assert
    assert [phase_result.name for phase_result in result.phases] == ["plan", "implement"]
    assert [call.phase_name for call in phase_runner.calls] == ["plan", "implement"]
    assert not result.success


@pytest.mark.asyncio
async def test_pipeline_skips_phases_with_failed_dependencies() -> None:
    # Arrange
    phases = [
        _phase("plan", required=False),
        _phase("implement", depends_on=["plan"]),
        _phase("review"),
    ]
    phase_runner = MockPhaseRunner(
        {
            "plan": _phase_result("plan", PhaseStatus.FAILED, error="planning failed"),
            "review": _phase_result("review", PhaseStatus.DONE),
        }
    )
    pipeline_runner = PipelineRunner(phase_runner=phase_runner)

    # Act
    result = await pipeline_runner.run_pipeline(
        phases=phases,
        prompts={phase.name: phase.name for phase in phases},
        ui_callback=NullCallback(),
    )

    # Assert
    assert [phase_result.name for phase_result in result.phases] == [
        "plan",
        "implement",
        "review",
    ]
    assert [phase_result.status for phase_result in result.phases] == [
        PhaseStatus.FAILED,
        PhaseStatus.SKIPPED,
        PhaseStatus.DONE,
    ]
    assert result.phases[1].error == "Dependencies not met: plan"
    assert [call.phase_name for call in phase_runner.calls] == ["plan", "review"]


@pytest.mark.asyncio
async def test_pipeline_skip_phases_parameter() -> None:
    # Arrange
    phases = [_phase("plan"), _phase("implement"), _phase("review")]
    phase_runner = MockPhaseRunner(
        {
            "plan": _phase_result("plan", PhaseStatus.DONE),
            "review": _phase_result("review", PhaseStatus.DONE),
        }
    )
    pipeline_runner = PipelineRunner(phase_runner=phase_runner)

    # Act
    result = await pipeline_runner.run_pipeline(
        phases=phases,
        prompts={phase.name: phase.name for phase in phases},
        ui_callback=NullCallback(),
        skip_phases=["implement"],
    )

    # Assert
    assert [phase_result.status for phase_result in result.phases] == [
        PhaseStatus.DONE,
        PhaseStatus.SKIPPED,
        PhaseStatus.DONE,
    ]
    assert [call.phase_name for call in phase_runner.calls] == ["plan", "review"]


@pytest.mark.asyncio
async def test_pipeline_only_phase_parameter() -> None:
    # Arrange
    phases = [_phase("plan"), _phase("implement"), _phase("review")]
    phase_runner = MockPhaseRunner({"implement": _phase_result("implement", PhaseStatus.DONE)})
    pipeline_runner = PipelineRunner(phase_runner=phase_runner)

    # Act
    result = await pipeline_runner.run_pipeline(
        phases=phases,
        prompts={"implement": "implement only"},
        ui_callback=NullCallback(),
        only_phase="implement",
    )

    # Assert
    assert result.phases == [_phase_result("implement", PhaseStatus.DONE)]
    assert phase_runner.calls == [
        PhaseInvocation(
            method="run_phase",
            phase_name="implement",
            prompt="implement only",
            mode=AgentMode.PLAN,
            toolset=None,
        )
    ]


@pytest.mark.asyncio
async def test_pipeline_passes_toolset_through_to_phase_runner() -> None:
    # Arrange
    tools = ToolSet(internal=["Read", "Write"], permission="workspace-write", max_turns=8)
    phase = _phase("implement", tools=tools)
    phase_runner = MockPhaseRunner({"implement": _phase_result("implement", PhaseStatus.DONE)})
    pipeline_runner = PipelineRunner(phase_runner=phase_runner)

    # Act
    await pipeline_runner.run_pipeline(
        phases=[phase],
        prompts={"implement": "use tools"},
        ui_callback=NullCallback(),
        mode=AgentMode.FIX,
    )

    # Assert
    assert phase_runner.calls == [
        PhaseInvocation(
            method="run_phase",
            phase_name="implement",
            prompt="use tools",
            mode=AgentMode.FIX,
            toolset=tools,
        )
    ]


@pytest.mark.asyncio
async def test_stream_pipeline_end_to_end(
    claude_jsonl_lines: list[str],
) -> None:
    # Arrange
    filter_stage = StreamFilter(StreamFormat.CLAUDE)
    parser_stage = StreamParser(StreamFormat.CLAUDE)
    stall_stage = StallDetector(
        normal_timeout=1.0,
        deep_timeout=2.0,
        check_interval=0.01,
    )
    monitor = AgentMonitor("claude")
    seen_events: list[StreamEvent] = []

    async def raw_lines() -> AsyncIterator[str]:
        for line in claude_jsonl_lines:
            yield line

    filtered = filter_stage.filter_stream(raw_lines())
    parsed = parser_stage.parse_stream(filtered)
    watched = stall_stage.watch(parsed)

    # Act
    await monitor.consume(watched, on_event=seen_events.append)
    snapshot = monitor.snapshot()

    # Assert
    assert [event.event_type for event in seen_events] == [
        StreamEventType.INIT,
        StreamEventType.TOOL_START,
        StreamEventType.TOOL_EXEC,
        StreamEventType.TOOL_DONE,
        StreamEventType.TEXT,
        StreamEventType.RESULT,
    ]
    assert snapshot.state is AgentState.COMPLETED
    assert snapshot.counters.total == 1
    assert snapshot.counters.succeeded == 1
    assert snapshot.text_count == 1
    assert snapshot.session_id == "sess-123"
    assert snapshot.cost_usd == Decimal("0.0123")
