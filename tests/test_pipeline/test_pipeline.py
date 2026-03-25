from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from orchcore.pipeline import Phase, PhaseResult, PhaseRunner, PhaseStatus, PipelineRunner
from orchcore.registry.agent import AgentMode, ToolSet
from orchcore.registry.registry import AgentRegistry
from orchcore.runner.subprocess import AgentRunner
from orchcore.ui.callback import NullCallback
from orchcore.workspace.manager import WorkspaceManager

if TYPE_CHECKING:
    from pathlib import Path

    from orchcore.ui.callback import UICallback


@dataclass(frozen=True)
class PhaseCall:
    method: str
    phase_name: str
    prompt: str
    mode: AgentMode
    toolset: ToolSet | None


class StubPhaseRunner(PhaseRunner):
    def __init__(self, results: dict[str, PhaseResult]) -> None:
        super().__init__(runner=AgentRunner(), registry=AgentRegistry())
        self._results = results
        self.calls: list[PhaseCall] = []

    async def run_phase(
        self,
        phase: Phase,
        prompt: str,
        ui_callback: UICallback,
        mode: AgentMode,
        toolset: ToolSet | None = None,
    ) -> PhaseResult:
        del ui_callback
        return self._record_and_return(
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
        return self._record_and_return(
            method="run_parallel",
            phase=phase,
            prompt=prompt,
            mode=mode,
            toolset=toolset,
        )

    def _record_and_return(
        self,
        *,
        method: str,
        phase: Phase,
        prompt: str,
        mode: AgentMode,
        toolset: ToolSet | None,
    ) -> PhaseResult:
        self.calls.append(
            PhaseCall(
                method=method,
                phase_name=phase.name,
                prompt=prompt,
                mode=mode,
                toolset=toolset,
            )
        )
        try:
            return self._results[phase.name]
        except KeyError as exc:
            raise AssertionError(f"Missing stubbed PhaseResult for phase {phase.name!r}") from exc


@pytest.fixture
def ui_callback() -> NullCallback:
    return NullCallback()


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceManager:
    manager = WorkspaceManager(project_root=tmp_path)
    manager.ensure_dirs(reuse=True)
    return manager


def _phase(
    name: str,
    *,
    depends_on: list[str] | None = None,
    required: bool = True,
    tools: ToolSet | None = None,
) -> Phase:
    return Phase(
        name=name,
        agents=[f"{name}-agent"],
        depends_on=[] if depends_on is None else depends_on,
        required=required,
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
async def test_run_pipeline_runs_single_phase(ui_callback: NullCallback) -> None:
    # Arrange
    shared_tools = ToolSet(internal=["Read"])
    planning = _phase("planning", tools=shared_tools)
    planning_result = _phase_result("planning", PhaseStatus.DONE)
    phase_runner = StubPhaseRunner({"planning": planning_result})
    pipeline_runner = PipelineRunner(phase_runner=phase_runner)

    # Act
    result = await pipeline_runner.run_pipeline(
        phases=[planning],
        prompts={"planning": "Draft the plan"},
        ui_callback=ui_callback,
    )

    # Assert
    assert result.phases == [planning_result]
    assert result.success is True
    assert phase_runner.calls == [
        PhaseCall(
            method="run_phase",
            phase_name="planning",
            prompt="Draft the plan",
            mode=AgentMode.PLAN,
            toolset=shared_tools,
        )
    ]


@pytest.mark.asyncio
async def test_run_pipeline_runs_phase_after_dependencies_complete(
    ui_callback: NullCallback,
) -> None:
    # Arrange
    phases = [
        _phase("planning"),
        _phase("implementation", depends_on=["planning"]),
    ]
    phase_runner = StubPhaseRunner(
        {
            "planning": _phase_result("planning", PhaseStatus.DONE),
            "implementation": _phase_result("implementation", PhaseStatus.DONE),
        }
    )
    pipeline_runner = PipelineRunner(phase_runner=phase_runner)

    # Act
    result = await pipeline_runner.run_pipeline(
        phases=phases,
        prompts={
            "planning": "Plan the work",
            "implementation": "Implement the work",
        },
        ui_callback=ui_callback,
    )

    # Assert
    assert [phase_result.name for phase_result in result.phases] == [
        "planning",
        "implementation",
    ]
    assert [phase_result.status for phase_result in result.phases] == [
        PhaseStatus.DONE,
        PhaseStatus.DONE,
    ]
    assert [call.phase_name for call in phase_runner.calls] == [
        "planning",
        "implementation",
    ]


@pytest.mark.asyncio
async def test_run_pipeline_skips_requested_phases(ui_callback: NullCallback) -> None:
    # Arrange
    phases = [
        _phase("planning"),
        _phase("implementation"),
        _phase("review"),
    ]
    phase_runner = StubPhaseRunner(
        {
            "planning": _phase_result("planning", PhaseStatus.DONE),
            "review": _phase_result("review", PhaseStatus.DONE),
        }
    )
    pipeline_runner = PipelineRunner(phase_runner=phase_runner)

    # Act
    result = await pipeline_runner.run_pipeline(
        phases=phases,
        prompts={phase.name: phase.name for phase in phases},
        ui_callback=ui_callback,
        skip_phases=["implementation"],
    )

    # Assert
    assert [phase_result.name for phase_result in result.phases] == [
        "planning",
        "implementation",
        "review",
    ]
    assert result.phases[1].status is PhaseStatus.SKIPPED
    assert result.phases[1].error == "Skipped by user request"
    assert [call.phase_name for call in phase_runner.calls] == [
        "planning",
        "review",
    ]


@pytest.mark.asyncio
async def test_run_pipeline_runs_only_selected_phase(ui_callback: NullCallback) -> None:
    # Arrange
    phases = [_phase("planning"), _phase("implementation")]
    implementation_result = _phase_result("implementation", PhaseStatus.DONE)
    phase_runner = StubPhaseRunner({"implementation": implementation_result})
    pipeline_runner = PipelineRunner(phase_runner=phase_runner)

    # Act
    result = await pipeline_runner.run_pipeline(
        phases=phases,
        prompts={"implementation": "Build it"},
        ui_callback=ui_callback,
        only_phase="implementation",
    )

    # Assert
    assert result.phases == [implementation_result]
    assert phase_runner.calls == [
        PhaseCall(
            method="run_phase",
            phase_name="implementation",
            prompt="Build it",
            mode=AgentMode.PLAN,
            toolset=None,
        )
    ]


@pytest.mark.asyncio
async def test_run_pipeline_resume_from_skips_earlier_unfinished_phases(
    ui_callback: NullCallback,
) -> None:
    # Arrange
    phases = [
        _phase("planning"),
        _phase("implementation"),
        _phase("review"),
    ]
    phase_runner = StubPhaseRunner(
        {
            "implementation": _phase_result("implementation", PhaseStatus.DONE),
            "review": _phase_result("review", PhaseStatus.DONE),
        }
    )
    pipeline_runner = PipelineRunner(phase_runner=phase_runner)

    # Act
    result = await pipeline_runner.run_pipeline(
        phases=phases,
        prompts={phase.name: phase.name for phase in phases},
        ui_callback=ui_callback,
        resume_from="implementation",
    )

    # Assert
    assert [phase_result.name for phase_result in result.phases] == [
        "planning",
        "implementation",
        "review",
    ]
    assert result.phases[0].status is PhaseStatus.SKIPPED
    assert result.phases[0].error == "Skipped (resuming from later phase)"
    assert [phase_result.status for phase_result in result.phases[1:]] == [
        PhaseStatus.DONE,
        PhaseStatus.DONE,
    ]
    assert [call.phase_name for call in phase_runner.calls] == [
        "implementation",
        "review",
    ]


@pytest.mark.asyncio
async def test_run_pipeline_stops_after_required_phase_failure(
    ui_callback: NullCallback,
) -> None:
    # Arrange
    phases = [_phase("planning"), _phase("implementation")]
    planning_result = _phase_result(
        "planning",
        PhaseStatus.FAILED,
        error="planning failed",
    )
    phase_runner = StubPhaseRunner(
        {
            "planning": planning_result,
            "implementation": _phase_result("implementation", PhaseStatus.DONE),
        }
    )
    pipeline_runner = PipelineRunner(phase_runner=phase_runner)

    # Act
    result = await pipeline_runner.run_pipeline(
        phases=phases,
        prompts={phase.name: phase.name for phase in phases},
        ui_callback=ui_callback,
    )

    # Assert
    assert result.phases == [planning_result]
    assert result.success is False
    assert [call.phase_name for call in phase_runner.calls] == ["planning"]


@pytest.mark.asyncio
async def test_run_pipeline_skips_phase_when_dependency_failed(
    ui_callback: NullCallback,
) -> None:
    # Arrange
    phases = [
        _phase("planning", required=False),
        _phase("implementation", depends_on=["planning"]),
    ]
    phase_runner = StubPhaseRunner(
        {
            "planning": _phase_result(
                "planning",
                PhaseStatus.FAILED,
                error="planning failed",
            ),
            "implementation": _phase_result("implementation", PhaseStatus.DONE),
        }
    )
    pipeline_runner = PipelineRunner(phase_runner=phase_runner)

    # Act
    result = await pipeline_runner.run_pipeline(
        phases=phases,
        prompts={phase.name: phase.name for phase in phases},
        ui_callback=ui_callback,
    )

    # Assert
    assert [phase_result.name for phase_result in result.phases] == [
        "planning",
        "implementation",
    ]
    assert result.phases[0].status is PhaseStatus.FAILED
    assert result.phases[1].status is PhaseStatus.SKIPPED
    assert result.phases[1].error == "Dependencies not met: planning"
    assert [call.phase_name for call in phase_runner.calls] == ["planning"]


@pytest.mark.asyncio
async def test_run_pipeline_saves_state_and_loads_it_for_resume(
    ui_callback: NullCallback,
    workspace: WorkspaceManager,
) -> None:
    # Arrange
    initial_phases = [_phase("planning"), _phase("implementation")]
    initial_runner = StubPhaseRunner(
        {
            "planning": _phase_result("planning", PhaseStatus.DONE),
            "implementation": _phase_result("implementation", PhaseStatus.DONE),
        }
    )
    pipeline_runner = PipelineRunner(
        phase_runner=initial_runner,
        workspace=workspace,
    )

    # Act
    await pipeline_runner.run_pipeline(
        phases=initial_phases,
        prompts={phase.name: phase.name for phase in initial_phases},
        ui_callback=ui_callback,
    )

    # Assert
    state_path = workspace.workspace_dir / ".state.json"
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "completed_phases": ["implementation", "planning"]
    }

    # Arrange
    resumed_phases = [
        _phase("planning"),
        _phase("implementation"),
        _phase("review"),
    ]
    resumed_runner = StubPhaseRunner({"review": _phase_result("review", PhaseStatus.DONE)})
    resumed_pipeline_runner = PipelineRunner(
        phase_runner=resumed_runner,
        workspace=workspace,
    )

    # Act
    result = await resumed_pipeline_runner.run_pipeline(
        phases=resumed_phases,
        prompts={phase.name: phase.name for phase in resumed_phases},
        ui_callback=ui_callback,
        resume_from="review",
    )

    # Assert
    assert [phase_result.name for phase_result in result.phases] == ["review"]
    assert [call.phase_name for call in resumed_runner.calls] == ["review"]
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "completed_phases": ["implementation", "planning", "review"]
    }
