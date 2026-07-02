"""PipelineRunner -- multi-phase orchestrator with resume/skip support."""

from __future__ import annotations

import heapq
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, TypedDict

from orchcore.pipeline.phase import Phase, PhaseResult, PhaseStatus, PipelineResult

if TYPE_CHECKING:
    from orchcore.pipeline.control import FlowControl
    from orchcore.pipeline.engine import PhaseRunner
    from orchcore.ui.callback import UICallback
    from orchcore.workspace.manager import WorkspaceManager

logger: logging.Logger = logging.getLogger(__name__)


class _PipelineState(TypedDict):
    """Serialized workspace state for pipeline resume support."""

    completed_phases: list[str]


class PipelineRunner:
    """Orchestrate multi-phase pipeline execution."""

    def __init__(
        self,
        phase_runner: PhaseRunner,
        workspace: WorkspaceManager | None = None,
        *,
        flow_control: FlowControl | None = None,
    ) -> None:
        self._phase_runner = phase_runner
        self._workspace = workspace
        self._flow_control = flow_control

    async def run_pipeline(
        self,
        phases: list[Phase],
        prompts: dict[str, str],
        ui_callback: UICallback,
        flag_profile: str | None = None,
        resume_from: str | None = None,
        skip_phases: list[str] | None = None,
        only_phase: str | None = None,
        allow_empty_prompts: bool = False,
    ) -> PipelineResult:
        """Execute a pipeline of phases in topological dependency order.

        Phases run dependencies-first; phases with no path between them keep
        their declaration order (stable ordering, see ADR-010). A *required*
        phase whose dependencies are unmet is recorded as ``SKIPPED``, fails
        the pipeline (``success=False``), and stops further execution —
        mirroring the required-phase failure rule. Optional phases skip
        freely without affecting ``success``.

        ``flag_profile`` is the pipeline-wide default flag profile; a phase
        with ``Phase.flag_profile`` set overrides it for that phase. ``None``
        selects no profile flags.
        """
        skip_set = set(skip_phases or [])
        ordered_phases = _validate_pipeline_request(
            phases=phases,
            prompts=prompts,
            resume_from=resume_from,
            only_phase=only_phase,
            skip_phases=skip_set,
            allow_empty_prompts=allow_empty_prompts,
        )

        started_at = datetime.now(UTC)
        phase_results: list[PhaseResult] = []
        completed_phases = await self._load_state() if resume_from is not None else set()
        resuming = resume_from is not None
        dependency_blocked_required = False

        ui_callback.on_pipeline_start(ordered_phases)

        for phase in ordered_phases:
            if only_phase is not None and phase.name != only_phase:
                continue

            if resuming:
                if phase.name == resume_from:
                    resuming = False
                elif phase.name in completed_phases:
                    reason = "Already completed (resuming)"
                    phase_results.append(_skipped_phase_result(name=phase.name, reason=reason))
                    ui_callback.on_phase_skip(phase, reason)
                    continue
                else:
                    reason = "Skipped (resuming from later phase)"
                    phase_results.append(_skipped_phase_result(name=phase.name, reason=reason))
                    ui_callback.on_phase_skip(phase, reason)
                    continue

            if phase.name in skip_set:
                reason = "Skipped by user request"
                phase_results.append(_skipped_phase_result(name=phase.name, reason=reason))
                ui_callback.on_phase_skip(phase, reason)
                continue

            if self._flow_control is not None:
                # Phase-boundary checkpoint: pause takes effect between
                # phases, and a pending skip request applies to the next
                # phase that would actually execute. A FlowControl skip is a
                # user-requested skip for the success semantics — though
                # skipping a required phase dependency-blocks its dependents.
                await self._flow_control.wait_if_paused()
                if self._flow_control.skip_requested:
                    self._flow_control.clear_skip()
                    reason = "Skipped via FlowControl"
                    phase_results.append(_skipped_phase_result(name=phase.name, reason=reason))
                    ui_callback.on_phase_skip(phase, reason)
                    continue

            unmet_dependencies = [
                dependency_name
                for dependency_name in phase.depends_on
                if dependency_name not in completed_phases
            ]
            if unmet_dependencies:
                # Ordering guarantees dependencies already had their turn, so
                # an unmet dependency here means it failed or was skipped.
                reason = f"Dependencies not met: {', '.join(unmet_dependencies)}"
                phase_results.append(_skipped_phase_result(name=phase.name, reason=reason))
                ui_callback.on_phase_skip(phase, reason)
                if phase.required:
                    dependency_blocked_required = True
                    logger.warning(
                        "Required phase %r blocked by unmet dependencies, stopping pipeline",
                        phase.name,
                    )
                    break
                continue

            prompt = prompts.get(phase.name, "")
            if phase.parallel and len(phase.agents) > 1:
                phase_result = await self._phase_runner.run_parallel(
                    phase=phase,
                    prompt=prompt,
                    ui_callback=ui_callback,
                    flag_profile=flag_profile,
                    toolset=phase.tools,
                )
            else:
                phase_result = await self._phase_runner.run_phase(
                    phase=phase,
                    prompt=prompt,
                    ui_callback=ui_callback,
                    flag_profile=flag_profile,
                    toolset=phase.tools,
                )

            phase_results.append(phase_result)

            if phase_result.status in {PhaseStatus.DONE, PhaseStatus.PARTIAL}:
                completed_phases.add(phase.name)

            if self._workspace is not None:
                try:
                    await self._save_state(completed_phases)
                except OSError as exc:
                    logger.warning(
                        "Failed to save pipeline resume state to '.state.json' after phase %r: %s",
                        phase.name,
                        exc,
                    )

            if phase_result.status is PhaseStatus.FAILED and phase.required:
                logger.warning("Required phase %r failed, stopping pipeline", phase.name)
                break

        pipeline_result = PipelineResult(
            phases=phase_results,
            total_duration=datetime.now(UTC) - started_at,
            total_cost_usd=_total_cost(phase_results),
            success=_pipeline_succeeded(
                phase_results,
                dependency_blocked_required=dependency_blocked_required,
            ),
        )
        ui_callback.on_pipeline_complete(pipeline_result)
        return pipeline_result

    async def _load_state(self) -> set[str]:
        """Load completed phase names from the workspace state file."""
        if self._workspace is None:
            return set()

        state_content = await self._workspace.aread_file(".state.json")
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

    async def _save_state(self, completed_phases: set[str]) -> None:
        """Save completed phase names to the workspace state file."""
        if self._workspace is None:
            return

        state_data: _PipelineState = {
            "completed_phases": sorted(completed_phases),
        }
        await self._workspace.awrite_file(".state.json", json.dumps(state_data, indent=2))


class PipelineError(Exception):
    """Base class for all orchcore pipeline errors."""


class EmptyPipelineError(PipelineError):
    """Raised when a pipeline has no phases to execute."""


class DuplicatePhaseError(PipelineError):
    """Raised when two or more phases share the same name."""


class UnknownAgentError(PipelineError):
    """Raised when a phase references an unknown dependency or agent name."""


class PipelineValidationError(PipelineError):
    """Raised when pipeline configuration is structurally invalid."""


def _validate_pipeline_request(
    *,
    phases: list[Phase],
    prompts: dict[str, str],
    resume_from: str | None,
    only_phase: str | None,
    skip_phases: set[str],
    allow_empty_prompts: bool,
) -> list[Phase]:
    """Validate run_pipeline inputs and return the topological execution order.

    Prompt validation mirrors the effective execution set: phases positioned
    before ``resume_from`` in the *ordered* sequence never execute on a
    resumed run, so they are exempt from the prompt check, exactly like
    ``skip_phases``/``only_phase`` exclusions.
    """
    if not phases:
        raise EmptyPipelineError("Pipeline must contain at least one phase")

    phase_names: set[str] = set()
    duplicate_phase_names: set[str] = set()

    for phase in phases:
        if phase.name in phase_names:
            duplicate_phase_names.add(phase.name)
        phase_names.add(phase.name)

    if duplicate_phase_names:
        duplicates = ", ".join(sorted(duplicate_phase_names))
        raise DuplicatePhaseError(f"Duplicate phase names are not allowed: {duplicates}")

    if resume_from is not None and resume_from not in phase_names:
        raise PipelineValidationError(f"Unknown resume_from phase: {resume_from!r}")

    if only_phase is not None and only_phase not in phase_names:
        raise PipelineValidationError(f"Unknown only_phase: {only_phase!r}")

    if resume_from is not None and only_phase is not None and resume_from != only_phase:
        raise PipelineValidationError(
            "resume_from and only_phase must reference the same phase when both are set"
        )

    phases_by_name = {phase.name: phase for phase in phases}
    unknown_dependencies = _collect_unknown_dependencies(phases_by_name)
    if unknown_dependencies:
        details = ", ".join(
            f"{phase_name} -> {', '.join(dependency_names)}"
            for phase_name, dependency_names in unknown_dependencies.items()
        )
        raise UnknownAgentError(f"Unknown depends_on phase(s): {details}")

    cycle_path = _find_dependency_cycle(phases_by_name)
    if cycle_path is not None:
        raise PipelineValidationError(f"Dependency cycle detected: {' -> '.join(cycle_path)}")

    ordered_phases = _topological_phases(phases)

    if allow_empty_prompts:
        return ordered_phases

    start_index = 0
    if resume_from is not None:
        start_index = next(
            index for index, phase in enumerate(ordered_phases) if phase.name == resume_from
        )

    missing_prompt_phases = [
        phase.name
        for phase in ordered_phases[start_index:]
        if phase.agents
        and phase.name not in skip_phases
        and (only_phase is None or phase.name == only_phase)
        and not prompts.get(phase.name)
    ]
    if missing_prompt_phases:
        missing = ", ".join(missing_prompt_phases)
        raise PipelineValidationError(
            f"No prompt provided for phase(s): {missing}. "
            "Pass allow_empty_prompts=True to permit promptless phases."
        )

    return ordered_phases


def _topological_phases(phases: list[Phase]) -> list[Phase]:
    """Return phases in stable topological order (Kahn's algorithm).

    Dependencies always precede their dependents; phases with no path between
    them keep their declaration order. Deterministic output matters for
    resume state and UX, so the ready queue is keyed on declaration index.
    Callers must have validated the graph first: unknown dependencies and
    cycles are rejected by ``_validate_pipeline_request`` before this runs.
    """
    index_by_name = {phase.name: index for index, phase in enumerate(phases)}
    pending_dependencies: dict[str, set[str]] = {
        phase.name: set(phase.depends_on) for phase in phases
    }
    dependents_by_name: dict[str, list[str]] = {phase.name: [] for phase in phases}
    for phase in phases:
        for dependency_name in set(phase.depends_on):
            dependents_by_name[dependency_name].append(phase.name)

    ready_indices = [
        index_by_name[name]
        for name, dependencies in pending_dependencies.items()
        if not dependencies
    ]
    heapq.heapify(ready_indices)

    ordered: list[Phase] = []
    while ready_indices:
        phase = phases[heapq.heappop(ready_indices)]
        ordered.append(phase)
        for dependent_name in dependents_by_name[phase.name]:
            remaining = pending_dependencies[dependent_name]
            remaining.discard(phase.name)
            if not remaining:
                heapq.heappush(ready_indices, index_by_name[dependent_name])

    return ordered


def _collect_unknown_dependencies(phases_by_name: dict[str, Phase]) -> dict[str, list[str]]:
    """Return unknown dependency names grouped by phase."""
    phase_names = set(phases_by_name)
    unknown_dependencies: dict[str, list[str]] = {}

    for phase_name, phase in phases_by_name.items():
        missing_dependencies = sorted(
            dependency_name
            for dependency_name in phase.depends_on
            if dependency_name not in phase_names
        )
        if missing_dependencies:
            unknown_dependencies[phase_name] = missing_dependencies

    return unknown_dependencies


def _find_dependency_cycle(phases_by_name: dict[str, Phase]) -> list[str] | None:
    """Return the first detected dependency cycle as an ordered phase path."""
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def _visit(phase_name: str) -> list[str] | None:
        if phase_name in visited:
            return None

        if phase_name in visiting:
            cycle_start = path.index(phase_name)
            return [*path[cycle_start:], phase_name]

        visiting.add(phase_name)
        path.append(phase_name)

        for dependency_name in phases_by_name[phase_name].depends_on:
            cycle_path = _visit(dependency_name)
            if cycle_path is not None:
                return cycle_path

        path.pop()
        visiting.remove(phase_name)
        visited.add(phase_name)
        return None

    for phase_name in phases_by_name:
        cycle_path = _visit(phase_name)
        if cycle_path is not None:
            return cycle_path

    return None


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


def _pipeline_succeeded(
    phase_results: list[PhaseResult],
    *,
    dependency_blocked_required: bool,
) -> bool:
    """Return whether the pipeline completed all required work.

    A pipeline succeeds when no phase failed *and* no required phase was
    skipped because its dependencies were unmet. User-requested skips
    (``skip_phases``, resume) still count as success.
    """
    if dependency_blocked_required:
        return False
    return all(
        result.status in {PhaseStatus.DONE, PhaseStatus.SKIPPED, PhaseStatus.PARTIAL}
        for result in phase_results
    )


__all__ = [
    "DuplicatePhaseError",
    "EmptyPipelineError",
    "PipelineError",
    "PipelineRunner",
    "PipelineValidationError",
    "UnknownAgentError",
]
