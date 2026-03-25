"""PhaseRunner -- sequential and parallel phase execution with signal handling."""

from __future__ import annotations

import asyncio
import contextlib
import re
import signal
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from orchcore.pipeline.phase import PhaseResult, PhaseStatus
from orchcore.stream.events import AgentResult

if TYPE_CHECKING:
    from orchcore.pipeline.phase import Phase
    from orchcore.registry.agent import AgentConfig, AgentMode, ToolSet
    from orchcore.registry.registry import AgentRegistry
    from orchcore.runner.subprocess import AgentRunner
    from orchcore.ui.callback import UICallback
    from orchcore.workspace.manager import WorkspaceManager

_DEFAULT_WORKSPACE_NAME = ".orchcore-workspace"
_OUTPUTS_DIRNAME = "outputs"
_OUTPUT_EXTENSION = ".md"
_PATH_COMPONENT_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


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
        workspace: WorkspaceManager | None = None,
        max_concurrency: int = 3,
        snapshot_interval: float | None = None,
        stall_check_interval: float = 5.0,
        telemetry: TelemetryProtocol | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError(
                f"max_concurrency must be at least 1, got {max_concurrency}"
            )
        if stall_check_interval <= 0:
            raise ValueError(
                "stall_check_interval must be greater than 0, "
                f"got {stall_check_interval}"
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
        self._fallback_workspace_dir = Path.cwd() / _DEFAULT_WORKSPACE_NAME
        self._workspace_ready = False

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

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        loop.call_later(5.0, self._force_kill_all)

    def _force_kill_all(self) -> None:
        """Kill any subprocess still running after the grace period."""
        for proc in list(self._active_processes):
            if proc.returncode is not None:
                continue
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

    async def run_phase(
        self,
        phase: Phase,
        prompt: str,
        ui_callback: UICallback,
        mode: AgentMode,
        toolset: ToolSet | None = None,
    ) -> PhaseResult:
        """Execute all agents in a phase sequentially."""
        self._install_signal_handlers()

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

        ui_callback.on_phase_start(phase)
        started_at = datetime.now()
        agent_results: list[AgentResult] = []
        current_agent_name = ""

        try:
            with _phase_span(self._telemetry, phase.name):
                for current_agent_name in phase.agents:
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
                        mode=mode,
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

        phase_result = _build_phase_result(
            phase_name=phase.name,
            started_at=started_at,
            agent_results=agent_results,
            allow_partial=False,
        )
        ui_callback.on_phase_end(phase, phase_result)
        return phase_result

    async def run_parallel(
        self,
        phase: Phase,
        prompt: str,
        ui_callback: UICallback,
        mode: AgentMode,
        toolset: ToolSet | None = None,
    ) -> PhaseResult:
        """Execute all agents in a phase concurrently."""
        self._install_signal_handlers()

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

        ui_callback.on_phase_start(phase)
        started_at = datetime.now()
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

        with _phase_span(self._telemetry, phase.name):
            coroutines = [
                self._run_with_semaphore(
                    agent=agent,
                    prompt=prompt,
                    output_path=output_path,
                    phase_name=phase.name,
                    ui_callback=ui_callback,
                    mode=mode,
                    toolset=effective_toolset,
                )
                for agent, output_path, effective_toolset in prepared_runs
            ]
            raw_results: list[AgentResult | BaseException] = await asyncio.gather(
                *coroutines,
                return_exceptions=True,
            )

        agent_results: list[AgentResult] = []
        for (agent, output_path, _toolset), outcome in zip(
            prepared_runs,
            raw_results,
            strict=True,
        ):
            if isinstance(outcome, BaseException):
                agent_result = _synthetic_agent_result(
                    agent_name=agent.name,
                    output_path=output_path,
                    phase_name=phase.name,
                    error=_exception_message(outcome),
                )
            else:
                agent_result = outcome

            agent_results.append(agent_result)
            self._emit_agent_callbacks(ui_callback, agent_result)

        phase_result = _build_phase_result(
            phase_name=phase.name,
            started_at=started_at,
            agent_results=agent_results,
            allow_partial=True,
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
        mode: AgentMode,
        toolset: ToolSet | None = None,
    ) -> AgentResult:
        """Acquire the phase semaphore before running an agent."""
        async with self._semaphore:
            ui_callback.on_agent_start(agent.name, phase_name)
            try:
                with _agent_span(self._telemetry, phase_name, agent.name):
                    return await self._runner.run(
                        agent=agent,
                        prompt=prompt,
                        output_path=output_path,
                        mode=mode,
                        on_event=ui_callback.on_agent_event,
                        snapshot_interval=self._snapshot_interval,
                        stall_check_interval=self._stall_check_interval,
                        on_process_start=self._register_process,
                        on_process_end=self._unregister_process,
                        toolset=toolset,
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
                )
            except OSError as exc:
                return _synthetic_agent_result(
                    agent_name=agent.name,
                    output_path=output_path,
                    phase_name=phase_name,
                    error=(
                        f"Agent {agent.name!r} failed in phase {phase_name!r}: {exc}"
                    ),
                )

    def terminate_active_processes(self) -> None:
        """Send SIGTERM to all currently running subprocesses."""
        for proc in list(self._active_processes):
            if proc.returncode is not None:
                continue
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()

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
        """Resolve the effective ToolSet for an agent invocation."""
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


def _build_phase_result(
    *,
    phase_name: str,
    started_at: datetime,
    agent_results: list[AgentResult],
    allow_partial: bool,
) -> PhaseResult:
    """Aggregate agent results into a single phase result."""
    duration = datetime.now() - started_at
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
        cost_usd=sum(costs, Decimal(0)) if costs else None,
    )


def _synthetic_agent_result(
    *,
    agent_name: str,
    output_path: Path,
    phase_name: str,
    error: str,
) -> AgentResult:
    """Create a synthetic AgentResult for launch/setup failures."""
    return AgentResult(
        agent_name=agent_name,
        output_path=output_path,
        stream_path=output_path.with_suffix(".stream"),
        log_path=output_path.with_suffix(".log"),
        exit_code=1,
        duration=timedelta(0),
        output_empty=True,
        error=f"{error} (phase={phase_name!r}, agent={agent_name!r})",
    )


def _agent_error_message(result: AgentResult) -> str | None:
    """Return a context-rich failure message when an agent did not succeed."""
    if result.exit_code == 0 and not result.output_empty:
        return None
    if result.error:
        return result.error
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
