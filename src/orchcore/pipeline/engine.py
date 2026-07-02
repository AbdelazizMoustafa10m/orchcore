"""PhaseRunner -- sequential and parallel phase execution with signal handling."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import signal
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from orchcore.pipeline.phase import PhaseResult, PhaseStatus
from orchcore.recovery import (
    BackoffStrategy,
    FailureMode,
    GitRecovery,
    RetryPolicy,
)
from orchcore.registry.agent import is_valid_flag_profile_name
from orchcore.runner.subprocess import kill_process_tree, terminate_process_tree
from orchcore.stream.events import AgentErrorCategory, AgentResult

if TYPE_CHECKING:
    from orchcore.pipeline.phase import Phase
    from orchcore.registry.agent import AgentConfig, ToolSet
    from orchcore.registry.registry import AgentRegistry
    from orchcore.runner.subprocess import AgentRunner
    from orchcore.ui.callback import UICallback
    from orchcore.workspace.manager import WorkspaceManager

_DEFAULT_WORKSPACE_NAME = ".orchcore-workspace"
_OUTPUTS_DIRNAME = "outputs"
_OUTPUT_EXTENSION = ".md"
_PATH_COMPONENT_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")

logger: logging.Logger = logging.getLogger(__name__)


def _validate_flag_profile_argument(flag_profile: str | None) -> None:
    """Reject malformed flag-profile fallback names at the call boundary.

    ``Phase.flag_profile`` is validated by the Phase model; this covers the
    plain-parameter path so a malformed name fails fast instead of degrading
    into a per-agent unknown-profile warning at command build time.
    """
    if flag_profile is not None and not is_valid_flag_profile_name(flag_profile):
        raise ValueError(f"Invalid flag profile name {flag_profile!r}")


class TelemetryProtocol(Protocol):
    """Minimal telemetry interface for optional OpenTelemetry integration."""

    def phase_span(
        self,
        phase: str,
        agent: str | None = None,
    ) -> contextlib.AbstractContextManager[object]: ...

    def agent_span(
        self,
        phase: str,
        agent: str,
    ) -> contextlib.AbstractContextManager[object]: ...


class PhaseRunner:
    """Execute a phase sequentially or in parallel.

    When no workspace is injected, artifacts are written under
    ``Path.cwd() / ".orchcore-workspace"``. The directory is created with
    ``exist_ok=True`` and is never wiped by this class.
    """

    def __init__(
        self,
        runner: AgentRunner,
        registry: AgentRegistry,
        *,
        workspace: WorkspaceManager | None = None,
        max_concurrency: int = 3,
        snapshot_interval: float | None = None,
        stall_check_interval: float = 5.0,
        telemetry: TelemetryProtocol | None = None,
        agent_cwd: Path | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be at least 1, got {max_concurrency}")
        if stall_check_interval <= 0:
            raise ValueError(
                f"stall_check_interval must be greater than 0, got {stall_check_interval}"
            )

        self._runner = runner
        self._registry = registry
        self._workspace = workspace
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._active_processes: list[asyncio.subprocess.Process] = []
        self._shutting_down = False
        self._signal_handlers_installed = False
        self._snapshot_interval = snapshot_interval
        self._stall_check_interval = stall_check_interval
        self._telemetry = telemetry
        self._agent_cwd = agent_cwd
        self._fallback_workspace_dir = Path.cwd() / _DEFAULT_WORKSPACE_NAME
        self._workspace_ready = False
        self._ui_callback: UICallback | None = None

    def _install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers on the running event loop."""
        if self._signal_handlers_installed:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._initiate_shutdown)
        except NotImplementedError:
            return

        self._signal_handlers_installed = True

    def _initiate_shutdown(self) -> None:
        """Gracefully terminate active subprocesses on the first signal."""
        if self._shutting_down:
            self._force_kill_all()
            return

        self._shutting_down = True
        self.terminate_active_processes()

        if self._ui_callback is not None:
            self._ui_callback.on_shutdown("Signal received")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        loop.call_later(30.0, self._force_kill_all)

    def _force_kill_all(self) -> None:
        """Kill any subprocess still running after the grace period."""
        for proc in list(self._active_processes):
            if proc.returncode is not None:
                continue
            with contextlib.suppress(ProcessLookupError):
                kill_process_tree(proc)

    def _check_phase_preconditions(
        self,
        phase: Phase,
        ui_callback: UICallback,
    ) -> PhaseResult | None:
        """Check shutdown and empty-agents conditions before executing a phase.

        Returns a ``PhaseResult`` if the phase should be short-circuited, or
        ``None`` when execution should proceed normally.
        """
        if self._shutting_down:
            result = _failed_phase(phase.name, "Shutdown in progress")
            ui_callback.on_phase_end(phase, result)
            return result

        if not phase.agents:
            result = _skipped_phase(
                phase.name,
                f"Phase {phase.name!r} has no configured agents",
            )
            ui_callback.on_phase_start(phase)
            ui_callback.on_phase_end(phase, result)
            return result

        return None

    async def run_phase(
        self,
        phase: Phase,
        prompt: str,
        ui_callback: UICallback,
        flag_profile: str | None = None,
        toolset: ToolSet | None = None,
    ) -> PhaseResult:
        """Execute all agents in a phase sequentially.

        ``flag_profile`` is the fallback profile for agents in this phase;
        ``Phase.flag_profile`` overrides it when set.
        """
        _validate_flag_profile_argument(flag_profile)
        self._install_signal_handlers()
        self._ui_callback = ui_callback

        early_result = self._check_phase_preconditions(phase, ui_callback)
        if early_result is not None:
            return early_result

        effective_profile = phase.flag_profile if phase.flag_profile is not None else flag_profile
        ui_callback.on_phase_start(phase)
        started_at = datetime.now(UTC)
        agent_results: list[AgentResult] = []
        current_agent_name = ""

        try:
            with _phase_span(self._telemetry, phase.name):
                for current_agent_name in phase.agents:
                    if self._shutting_down:
                        break
                    agent = self._resolve_agent(current_agent_name, phase.name)
                    output_path = await self._resolve_output_path(
                        phase_name=phase.name,
                        agent_name=agent.name,
                    )
                    agent_result = await self._run_with_semaphore(
                        agent=agent,
                        prompt=prompt,
                        output_path=output_path,
                        phase_name=phase.name,
                        ui_callback=ui_callback,
                        flag_profile=effective_profile,
                        phase_failure_mode=phase.failure_mode,
                        retry_policy=phase.retry_policy,
                        toolset=self._resolve_toolset(
                            phase=phase,
                            agent_name=agent.name,
                            explicit_toolset=toolset,
                        ),
                    )
                    agent_results.append(agent_result)
                    self._emit_agent_callbacks(ui_callback, agent_result)
        except KeyError:
            result = _failed_phase(
                phase.name,
                f"Phase {phase.name!r} references unknown agent {current_agent_name!r}",
            )
            ui_callback.on_phase_end(phase, result)
            return result
        except OSError as exc:
            result = _failed_phase(
                phase.name,
                f"Failed to prepare output paths for phase {phase.name!r}: {exc}",
            )
            ui_callback.on_phase_end(phase, result)
            return result

        policy = phase.retry_policy or RetryPolicy(failure_mode=phase.failure_mode)
        succeeded = sum(1 for r in agent_results if _agent_error_message(r) is None)
        failed = len(agent_results) - succeeded
        allow_partial = (
            policy.evaluate_results(
                succeeded=succeeded,
                failed=failed,
                total=len(agent_results),
            )
            == "partial"
        )
        phase_result = _build_phase_result(
            phase_name=phase.name,
            started_at=started_at,
            agent_results=agent_results,
            allow_partial=allow_partial,
        )
        ui_callback.on_phase_end(phase, phase_result)
        return phase_result

    async def run_parallel(
        self,
        phase: Phase,
        prompt: str,
        ui_callback: UICallback,
        flag_profile: str | None = None,
        toolset: ToolSet | None = None,
    ) -> PhaseResult:
        """Execute all agents in a phase concurrently.

        ``flag_profile`` is the fallback profile for agents in this phase;
        ``Phase.flag_profile`` overrides it when set.
        """
        _validate_flag_profile_argument(flag_profile)
        self._install_signal_handlers()
        self._ui_callback = ui_callback

        early_result = self._check_phase_preconditions(phase, ui_callback)
        if early_result is not None:
            return early_result

        effective_profile = phase.flag_profile if phase.flag_profile is not None else flag_profile
        ui_callback.on_phase_start(phase)
        started_at = datetime.now(UTC)
        current_agent_name = ""

        try:
            prepared_runs: list[tuple[AgentConfig, Path, ToolSet | None]] = []
            for current_agent_name in phase.agents:
                agent = self._resolve_agent(current_agent_name, phase.name)
                output_path = await self._resolve_output_path(
                    phase_name=phase.name,
                    agent_name=agent.name,
                )
                prepared_runs.append(
                    (
                        agent,
                        output_path,
                        self._resolve_toolset(
                            phase=phase,
                            agent_name=agent.name,
                            explicit_toolset=toolset,
                        ),
                    )
                )
        except KeyError:
            result = _failed_phase(
                phase.name,
                f"Phase {phase.name!r} references unknown agent {current_agent_name!r}",
            )
            ui_callback.on_phase_end(phase, result)
            return result
        except OSError as exc:
            result = _failed_phase(
                phase.name,
                f"Failed to prepare output paths for phase {phase.name!r}: {exc}",
            )
            ui_callback.on_phase_end(phase, result)
            return result

        policy = phase.retry_policy or RetryPolicy(failure_mode=phase.failure_mode)

        with _phase_span(self._telemetry, phase.name):
            raw_results: list[AgentResult | BaseException]
            if policy.failure_mode is FailureMode.FAIL_FAST:
                task_by_agent_name = {
                    agent.name: asyncio.create_task(
                        self._run_with_semaphore(
                            agent=agent,
                            prompt=prompt,
                            output_path=output_path,
                            phase_name=phase.name,
                            ui_callback=ui_callback,
                            flag_profile=effective_profile,
                            phase_failure_mode=phase.failure_mode,
                            retry_policy=policy,
                            toolset=effective_toolset,
                        )
                    )
                    for agent, output_path, effective_toolset in prepared_runs
                }
                agent_by_task = {
                    task: agent
                    for agent, task in (
                        (agent, task_by_agent_name[agent.name]) for agent, _, _ in prepared_runs
                    )
                }
                raw_results_by_agent: dict[str, AgentResult | BaseException] = {}
                pending_tasks = set(agent_by_task)
                stop_on_failure = False

                try:
                    while pending_tasks:
                        done_tasks, pending_tasks = await asyncio.wait(
                            pending_tasks,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for completed_task in done_tasks:
                            agent = agent_by_task[completed_task]
                            try:
                                outcome = await completed_task
                            except Exception as exc:
                                raw_results_by_agent[agent.name] = exc
                                stop_on_failure = True
                            else:
                                raw_results_by_agent[agent.name] = outcome
                                if _agent_error_message(outcome) is not None:
                                    stop_on_failure = True

                        if not stop_on_failure:
                            continue

                        for pending_task in pending_tasks:
                            pending_task.cancel()

                        pending_task_list = list(pending_tasks)
                        pending_results = await asyncio.gather(
                            *pending_task_list,
                            return_exceptions=True,
                        )
                        for pending_task, pending_result in zip(
                            pending_task_list,
                            pending_results,
                            strict=True,
                        ):
                            pending_agent = agent_by_task[pending_task]
                            if isinstance(pending_result, asyncio.CancelledError):
                                raw_results_by_agent[pending_agent.name] = _FailFastCancellation(
                                    "Cancelled due to fail-fast sibling failure"
                                )
                            else:
                                raw_results_by_agent[pending_agent.name] = pending_result
                        break
                finally:
                    for task in agent_by_task:
                        if task.done():
                            continue
                        task.cancel()

                raw_results = [raw_results_by_agent[agent.name] for agent, _, _ in prepared_runs]
            else:
                coroutines = [
                    self._run_with_semaphore(
                        agent=agent,
                        prompt=prompt,
                        output_path=output_path,
                        phase_name=phase.name,
                        ui_callback=ui_callback,
                        flag_profile=effective_profile,
                        phase_failure_mode=phase.failure_mode,
                        retry_policy=policy,
                        toolset=effective_toolset,
                    )
                    for agent, output_path, effective_toolset in prepared_runs
                ]
                # Preserve per-agent outcomes for partial-failure evaluation.
                raw_results = await asyncio.gather(
                    *coroutines,
                    return_exceptions=True,
                )

        agent_results: list[AgentResult] = []
        for (agent, output_path, _toolset), raw_outcome in zip(
            prepared_runs,
            raw_results,
            strict=True,
        ):
            if isinstance(raw_outcome, AgentResult):
                agent_result = raw_outcome
            else:
                agent_result = _synthetic_agent_result(
                    agent_name=agent.name,
                    output_path=output_path,
                    phase_name=phase.name,
                    error=_exception_message(raw_outcome),
                    category=_exception_category(raw_outcome),
                )

            agent_results.append(agent_result)
            self._emit_agent_callbacks(ui_callback, agent_result)

        succeeded = sum(
            1 for agent_result in agent_results if _agent_error_message(agent_result) is None
        )
        failed = len(agent_results) - succeeded
        evaluated_status = policy.evaluate_results(
            succeeded=succeeded,
            failed=failed,
            total=len(agent_results),
        )
        duration = datetime.now(UTC) - started_at
        output_files = [
            r.output_path for r in agent_results if not r.output_empty and r.output_path is not None
        ]
        error_messages = [
            error_message
            for agent_result in agent_results
            if (error_message := _agent_error_message(agent_result)) is not None
        ]
        costs = [
            agent_result.cost_usd
            for agent_result in agent_results
            if agent_result.cost_usd is not None
        ]
        status_map = {
            "done": PhaseStatus.DONE,
            "partial": PhaseStatus.PARTIAL,
            "failed": PhaseStatus.FAILED,
        }
        phase_result = PhaseResult(
            name=phase.name,
            status=status_map[evaluated_status],
            duration=duration,
            output_files=output_files,
            agent_results=agent_results,
            error="; ".join(error_messages) if error_messages else None,
            error_messages=error_messages,
            cost_usd=sum(costs, Decimal(0)) if costs else None,
        )
        ui_callback.on_phase_end(phase, phase_result)
        return phase_result

    async def _run_with_semaphore(
        self,
        agent: AgentConfig,
        prompt: str,
        output_path: Path,
        phase_name: str,
        ui_callback: UICallback,
        flag_profile: str | None,
        phase_failure_mode: FailureMode,
        retry_policy: RetryPolicy | None = None,
        toolset: ToolSet | None = None,
    ) -> AgentResult:
        """Acquire the phase semaphore before running an agent."""
        async with self._semaphore:
            ui_callback.on_agent_start(agent.name, phase_name)
            policy = retry_policy or RetryPolicy(failure_mode=phase_failure_mode)
            backoff_strategy = BackoffStrategy(
                schedule=policy.backoff_schedule,
                max_wait=policy.max_wait,
            )
            effective_cwd = self._resolve_agent_cwd()
            attempt = 1

            while True:
                if self._shutting_down:
                    return _synthetic_agent_result(
                        agent_name=agent.name,
                        output_path=output_path,
                        phase_name=phase_name,
                        error=f"Agent {agent.name!r} aborted: shutdown in progress",
                        category=AgentErrorCategory.CANCELLED,
                    )
                try:
                    with _agent_span(self._telemetry, phase_name, agent.name):
                        result = await self._runner.run(
                            agent=agent,
                            prompt=prompt,
                            output_path=output_path,
                            flag_profile=flag_profile,
                            on_event=ui_callback.on_agent_event,
                            snapshot_interval=self._snapshot_interval,
                            stall_check_interval=self._stall_check_interval,
                            on_process_start=self._register_process,
                            on_process_end=self._unregister_process,
                            toolset=toolset,
                            on_stall=ui_callback.on_stall_detected,
                            cwd=effective_cwd,
                        )
                except FileNotFoundError as exc:
                    return _synthetic_agent_result(
                        agent_name=agent.name,
                        output_path=output_path,
                        phase_name=phase_name,
                        error=(
                            f"Agent {agent.name!r} binary {agent.binary!r} could not be "
                            f"started in phase {phase_name!r}: {exc}"
                        ),
                        category=AgentErrorCategory.BINARY_NOT_FOUND,
                    )
                except OSError as exc:
                    return _synthetic_agent_result(
                        agent_name=agent.name,
                        output_path=output_path,
                        phase_name=phase_name,
                        error=(f"Agent {agent.name!r} failed in phase {phase_name!r}: {exc}"),
                        category=AgentErrorCategory.OS_ERROR,
                    )

                # Retry decisions key on the typed category populated by the
                # runner; no error-string matching happens at this level.
                if result.error_category is not AgentErrorCategory.RATE_LIMIT:
                    return result
                if not policy.should_retry(attempt):
                    return result

                message = (result.error or "").strip()
                if not message:
                    message = f"Agent {agent.name!r} hit a rate limit"

                ui_callback.on_rate_limit(agent.name, message)
                wait_seconds = backoff_strategy.compute_wait(
                    attempt,
                    reset_seconds=result.rate_limit_reset_seconds,
                )
                ui_callback.on_rate_limit_wait(agent.name, wait_seconds)

                recovery = await self._apply_git_recovery(
                    policy=policy,
                    effective_cwd=effective_cwd,
                    ui_callback=ui_callback,
                )

                ui_callback.on_retry(agent.name, attempt, policy.max_retries)
                if self._shutting_down:
                    if recovery is not None:
                        await _restore_git_stash_if_needed(recovery, ui_callback)
                    return result
                await asyncio.sleep(wait_seconds)
                if recovery is not None:
                    await _restore_git_stash_if_needed(recovery, ui_callback)
                attempt += 1

    def terminate_active_processes(self) -> None:
        """Send SIGTERM to all currently running subprocesses."""
        for proc in list(self._active_processes):
            if proc.returncode is not None:
                continue
            with contextlib.suppress(ProcessLookupError):
                terminate_process_tree(proc)

    def _register_process(self, proc: asyncio.subprocess.Process) -> None:
        """Track a live subprocess so signal handlers can terminate it."""
        self._active_processes.append(proc)

    def _unregister_process(self, proc: asyncio.subprocess.Process) -> None:
        """Stop tracking a subprocess once it has exited."""
        if proc in self._active_processes:
            self._active_processes.remove(proc)

    def _resolve_agent(self, agent_name: str, phase_name: str) -> AgentConfig:
        """Resolve an agent name through the injected registry."""
        try:
            return self._registry.get(agent_name)
        except KeyError as exc:
            msg = f"Phase {phase_name!r} references unknown agent {agent_name!r}"
            raise KeyError(msg) from exc

    def _resolve_toolset(
        self,
        phase: Phase,
        agent_name: str,
        explicit_toolset: ToolSet | None,
    ) -> ToolSet | None:
        """Resolve the effective ToolSet for an agent invocation.

        Priority (highest first):
        1. ``Phase.agent_tools[agent_name]``
        2. ``explicit_toolset``
        3. ``Phase.tools``
        4. ``None`` -> no ToolSet translation. Flag profiles are independent
           of this resolution: a selected profile's flags always apply.
        """
        if agent_name in phase.agent_tools:
            return phase.agent_tools[agent_name]
        if explicit_toolset is not None:
            return explicit_toolset
        if phase.tools is not None:
            return phase.tools
        return None

    async def _resolve_output_path(self, phase_name: str, agent_name: str) -> Path:
        """Create and return the output path for a phase/agent pair."""
        workspace_dir = await self._workspace_root()
        output_dir = workspace_dir / _OUTPUTS_DIRNAME / _path_component(phase_name)
        await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
        return output_dir / f"{_path_component(agent_name)}{_OUTPUT_EXTENSION}"

    def _resolve_agent_cwd(self) -> Path | None:
        """Return the cwd used for agent subprocesses and git recovery."""
        if self._agent_cwd is not None:
            return self._agent_cwd
        if self._workspace is not None:
            return self._workspace.project_root
        return None

    async def _apply_git_recovery(
        self,
        *,
        policy: RetryPolicy,
        effective_cwd: Path | None,
        ui_callback: UICallback,
    ) -> GitRecovery | None:
        """Run opt-in git recovery and return a stash recovery to restore, if any."""
        if policy.git_recovery == "off":
            return None

        recovery_cwd = policy.git_recovery_cwd or effective_cwd
        if recovery_cwd is None:
            logger.warning(
                "Skipping git recovery mode %r because no git_recovery_cwd or agent cwd is set",
                policy.git_recovery,
            )
            ui_callback.on_git_recovery(
                "skipped_no_cwd",
                "git recovery skipped because no cwd was resolved",
            )
            return None

        git_recovery = GitRecovery(working_dir=str(recovery_cwd))
        if policy.git_recovery == "auto_commit":
            if await git_recovery.is_tree_dirty() and await git_recovery.auto_commit(
                no_verify=policy.git_recovery_no_verify
            ):
                ui_callback.on_git_recovery(
                    "auto_commit",
                    "cleaned dirty tree before retry",
                )
            return None

        if await git_recovery.stash_dirty_tree():
            ui_callback.on_git_recovery("stash", "stashed dirty tree before retry")
            return git_recovery

        return None

    async def _workspace_root(self) -> Path:
        """Return the active workspace directory, creating it if needed."""
        if self._workspace_ready:
            if self._workspace is not None:
                return self._workspace.workspace_dir
            return self._fallback_workspace_dir

        if self._workspace is not None:
            await asyncio.to_thread(self._workspace.ensure_dirs, True)
            self._workspace_ready = True
            return self._workspace.workspace_dir

        await asyncio.to_thread(
            self._fallback_workspace_dir.mkdir,
            parents=True,
            exist_ok=True,
        )
        self._workspace_ready = True
        return self._fallback_workspace_dir

    @staticmethod
    def _emit_agent_callbacks(
        ui_callback: UICallback,
        result: AgentResult,
    ) -> None:
        """Emit orchcore UI callbacks for an agent result."""
        error_message = _agent_error_message(result)
        if error_message is not None:
            ui_callback.on_agent_error(result.agent_name, error_message)
        ui_callback.on_agent_complete(result.agent_name, result)


def _phase_span(
    telemetry: TelemetryProtocol | None,
    phase: str,
    *,
    agent: str | None = None,
) -> contextlib.AbstractContextManager[object]:
    """Return a telemetry phase span or a no-op context manager."""
    if telemetry is None:
        return contextlib.nullcontext()
    return telemetry.phase_span(phase, agent=agent)


def _agent_span(
    telemetry: TelemetryProtocol | None,
    phase: str,
    agent: str,
) -> contextlib.AbstractContextManager[object]:
    """Return a telemetry agent span or a no-op context manager."""
    if telemetry is None:
        return contextlib.nullcontext()
    return telemetry.agent_span(phase, agent)


async def _restore_git_stash_if_needed(
    git_recovery: GitRecovery,
    ui_callback: UICallback,
) -> None:
    """Restore a retry stash without raising into the retry loop."""
    if await git_recovery.restore_stash():
        ui_callback.on_git_recovery("stash_restore", "restored dirty tree after retry wait")
        return

    logger.warning("Git stash restore failed after retry wait")
    ui_callback.on_git_recovery(
        "stash_restore_failed",
        "git stash restore failed after retry wait",
    )


def _build_phase_result(
    *,
    phase_name: str,
    started_at: datetime,
    agent_results: list[AgentResult],
    allow_partial: bool,
) -> PhaseResult:
    """Aggregate agent results into a single phase result."""
    duration = datetime.now(UTC) - started_at
    output_files = [
        output_path
        for result in agent_results
        if not result.output_empty and (output_path := result.output_path) is not None
    ]
    error_messages = [
        error_message
        for result in agent_results
        if (error_message := _agent_error_message(result)) is not None
    ]
    costs = [result.cost_usd for result in agent_results if result.cost_usd is not None]

    if not error_messages:
        status = PhaseStatus.DONE
    elif allow_partial and len(error_messages) < len(agent_results):
        status = PhaseStatus.PARTIAL
    else:
        status = PhaseStatus.FAILED

    return PhaseResult(
        name=phase_name,
        status=status,
        duration=duration,
        output_files=output_files,
        agent_results=agent_results,
        error="; ".join(error_messages) if error_messages else None,
        error_messages=error_messages,
        cost_usd=sum(costs, Decimal(0)) if costs else None,
    )


class _FailFastCancellation(RuntimeError):
    """Marks agents cancelled because a fail-fast sibling failed."""


def _exception_category(error: BaseException) -> AgentErrorCategory:
    """Map an exception escaping an agent task onto a result category."""
    if isinstance(error, _FailFastCancellation | asyncio.CancelledError):
        return AgentErrorCategory.CANCELLED
    return AgentErrorCategory.OS_ERROR


def _synthetic_agent_result(
    *,
    agent_name: str,
    output_path: Path,
    phase_name: str,
    error: str,
    category: AgentErrorCategory,
) -> AgentResult:
    """Create a synthetic AgentResult for launch/setup/cancellation failures."""
    return AgentResult(
        agent_name=agent_name,
        output_path=output_path,
        stream_path=output_path.with_suffix(".stream"),
        log_path=output_path.with_suffix(".log"),
        exit_code=1,
        duration=timedelta(0),
        output_empty=True,
        error=f"{error} (phase={phase_name!r}, agent={agent_name!r})",
        error_category=category,
    )


def _agent_error_message(result: AgentResult) -> str | None:
    """Return a context-rich failure message when an agent did not succeed."""
    if result.error:
        return result.error
    if result.exit_code == 0 and not result.output_empty:
        return None
    if result.output_empty:
        return f"Agent {result.agent_name!r} completed without producing output"
    return f"Agent {result.agent_name!r} exited with code {result.exit_code}"


def _exception_message(error: BaseException) -> str:
    """Normalize exception text for synthetic agent failures."""
    message = str(error).strip()
    if message:
        return message
    return type(error).__name__


def _path_component(value: str) -> str:
    """Convert arbitrary phase and agent names into safe path components."""
    normalized = _PATH_COMPONENT_PATTERN.sub("-", value.strip()).strip("-._")
    return normalized or "unnamed"


def _failed_phase(name: str, reason: str) -> PhaseResult:
    """Return a failed phase result without timing metadata."""
    return PhaseResult(
        name=name,
        status=PhaseStatus.FAILED,
        error=reason,
    )


def _skipped_phase(name: str, reason: str) -> PhaseResult:
    """Return a skipped phase result without timing metadata."""
    return PhaseResult(
        name=name,
        status=PhaseStatus.SKIPPED,
        error=reason,
    )


__all__ = ["PhaseRunner", "TelemetryProtocol"]
