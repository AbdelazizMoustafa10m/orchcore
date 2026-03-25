"""PipelineRunner -- multi-phase orchestrator with resume/skip support."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, TypedDict

from orchcore.pipeline.phase import Phase, PhaseResult, PhaseStatus, PipelineResult
from orchcore.registry.agent import AgentMode

if TYPE_CHECKING:
    from orchcore.pipeline.engine import PhaseRunner
    from orchcore.ui.callback import UICallback
    from orchcore.workspace.manager import WorkspaceManager

logger = logging.getLogger(__name__)


class _PipelineState(TypedDict):
    """Serialized workspace state for pipeline resume support."""

    completed_phases: list[str]


class PipelineRunner:
    """Orchestrate multi-phase pipeline execution."""

    def __init__(
        self,
        phase_runner: PhaseRunner,
        workspace: WorkspaceManager | None = None,
    ) -> None:
        self._phase_runner = phase_runner
        self._workspace = workspace

    async def run_pipeline(
        self,
        phases: list[Phase],
        prompts: dict[str, str],
        ui_callback: UICallback,
        mode: AgentMode | None = None,
        resume_from: str | None = None,
        skip_phases: list[str] | None = None,
        only_phase: str | None = None,
    ) -> PipelineResult:
        """Execute a pipeline of phases in order."""
        _validate_pipeline_request(
            phases=phases,
            resume_from=resume_from,
            only_phase=only_phase,
        )

        effective_mode = AgentMode.PLAN if mode is None else mode
        skip_set = set(skip_phases or [])
        started_at = datetime.now()
        phase_results: list[PhaseResult] = []
        completed_phases = self._load_state() if resume_from is not None else set()
        resuming = resume_from is not None

        ui_callback.on_pipeline_start(phases)

        for phase in phases:
            if only_phase is not None and phase.name != only_phase:
                continue

            if resuming:
                if phase.name == resume_from:
                    resuming = False
                elif phase.name in completed_phases:
                    continue
                else:
                    phase_results.append(
                        _skipped_phase_result(
                            name=phase.name,
                            reason="Skipped (resuming from later phase)",
                        )
                    )
                    continue

            if phase.name in skip_set:
                reason = "Skipped by user request"
                phase_results.append(_skipped_phase_result(name=phase.name, reason=reason))
                ui_callback.on_phase_skip(phase, reason)
                continue

            unmet_dependencies = [
                dependency_name
                for dependency_name in phase.depends_on
                if dependency_name not in completed_phases
            ]
            if unmet_dependencies:
                reason = f"Dependencies not met: {', '.join(unmet_dependencies)}"
                phase_results.append(_skipped_phase_result(name=phase.name, reason=reason))
                ui_callback.on_phase_skip(phase, reason)
                continue

            prompt = prompts.get(phase.name, "")
            if phase.parallel and len(phase.agents) > 1:
                phase_result = await self._phase_runner.run_parallel(
                    phase=phase,
                    prompt=prompt,
                    ui_callback=ui_callback,
                    mode=effective_mode,
                    toolset=phase.tools,
                )
            else:
                phase_result = await self._phase_runner.run_phase(
                    phase=phase,
                    prompt=prompt,
                    ui_callback=ui_callback,
                    mode=effective_mode,
                    toolset=phase.tools,
                )

            phase_results.append(phase_result)

            if phase_result.status in {PhaseStatus.DONE, PhaseStatus.PARTIAL}:
                completed_phases.add(phase.name)

            if self._workspace is not None:
                self._save_state(completed_phases)

            if phase_result.status is PhaseStatus.FAILED and phase.required:
                logger.warning("Required phase %r failed, stopping pipeline", phase.name)
                break

        pipeline_result = PipelineResult(
            phases=phase_results,
            total_duration=datetime.now() - started_at,
            total_cost_usd=_total_cost(phase_results),
            success=_pipeline_succeeded(phase_results),
        )
        ui_callback.on_pipeline_complete(pipeline_result)
        return pipeline_result

    def _load_state(self) -> set[str]:
        """Load completed phase names from the workspace state file."""
        if self._workspace is None:
            return set()

        state_content = self._workspace.read_file(".state.json")
        if state_content is None:
            return set()

        try:
            raw_state: object = json.loads(state_content)
        except json.JSONDecodeError:
            return set()

        if not isinstance(raw_state, dict):
            return set()

        completed_phases = raw_state.get("completed_phases")
        if not isinstance(completed_phases, list):
            return set()

        return {phase_name for phase_name in completed_phases if isinstance(phase_name, str)}

    def _save_state(self, completed_phases: set[str]) -> None:
        """Save completed phase names to the workspace state file."""
        if self._workspace is None:
            return

        state_data: _PipelineState = {
            "completed_phases": sorted(completed_phases),
        }
        self._workspace.write_file(".state.json", json.dumps(state_data, indent=2))


def _validate_pipeline_request(
    *,
    phases: list[Phase],
    resume_from: str | None,
    only_phase: str | None,
) -> None:
    """Validate public run_pipeline inputs before execution starts."""
    phase_names: set[str] = set()
    duplicate_phase_names: set[str] = set()

    for phase in phases:
        if phase.name in phase_names:
            duplicate_phase_names.add(phase.name)
        phase_names.add(phase.name)

    if duplicate_phase_names:
        duplicates = ", ".join(sorted(duplicate_phase_names))
        raise ValueError(f"Duplicate phase names are not allowed: {duplicates}")

    if resume_from is not None and resume_from not in phase_names:
        raise ValueError(f"Unknown resume_from phase: {resume_from!r}")

    if only_phase is not None and only_phase not in phase_names:
        raise ValueError(f"Unknown only_phase: {only_phase!r}")

    if resume_from is not None and only_phase is not None and resume_from != only_phase:
        raise ValueError(
            "resume_from and only_phase must reference the same phase when both are set"
        )


def _skipped_phase_result(*, name: str, reason: str) -> PhaseResult:
    """Build a skipped phase result with a user-visible reason."""
    return PhaseResult(
        name=name,
        status=PhaseStatus.SKIPPED,
        error=reason,
    )


def _total_cost(phase_results: list[PhaseResult]) -> Decimal | None:
    """Aggregate per-phase cost values into a pipeline total."""
    costs = [result.cost_usd for result in phase_results if result.cost_usd is not None]
    if not costs:
        return None
    return sum(costs, Decimal(0))


def _pipeline_succeeded(phase_results: list[PhaseResult]) -> bool:
    """Return whether the pipeline completed without a failed phase result."""
    return all(
        result.status in {PhaseStatus.DONE, PhaseStatus.SKIPPED, PhaseStatus.PARTIAL}
        for result in phase_results
    )


__all__ = ["PipelineRunner"]
