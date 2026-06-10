from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
import textwrap
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from orchcore.pipeline.engine import (
    PhaseRunner,
    _agent_error_message,
    _agent_span,
    _build_phase_result,
    _exception_message,
    _failed_phase,
    _path_component,
    _phase_span,
    _restore_git_stash_if_needed,
    _skipped_phase,
    _synthetic_agent_result,
)
from orchcore.pipeline.phase import Phase, PhaseResult, PhaseStatus
from orchcore.recovery import FailureMode, GitRecovery, RetryPolicy
from orchcore.registry.agent import AgentConfig, AgentMode, ToolSet
from orchcore.registry.registry import AgentRegistry
from orchcore.runner.subprocess import AgentRunner
from orchcore.stream.events import AgentErrorCategory, AgentResult
from orchcore.ui.callback import NullCallback

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class FakeLoop:
    def __init__(self) -> None:
        self.scheduled_callbacks: list[tuple[float, Callable[[], None]]] = []
        self.added_signal_handlers: list[tuple[signal.Signals, Callable[[], None]]] = []

    def call_later(self, delay: float, callback: Callable[[], None]) -> None:
        self.scheduled_callbacks.append((delay, callback))

    def add_signal_handler(self, sig: signal.Signals, callback: Callable[[], None]) -> None:
        self.added_signal_handlers.append((sig, callback))


class FakeProcess:
    def __init__(self, *, returncode: int | None = None, raise_on_kill: bool = False) -> None:
        self.returncode = returncode
        self.raise_on_kill = raise_on_kill
        self.kill_calls = 0
        self.terminate_calls = 0
        self.pid = 12345

    def kill(self) -> None:
        self.kill_calls += 1
        if self.raise_on_kill:
            raise ProcessLookupError

    def terminate(self) -> None:
        self.terminate_calls += 1


class _RecordingCallback(NullCallback):
    def __init__(self) -> None:
        self.rate_limits: list[tuple[str, str]] = []
        self.rate_limit_waits: list[tuple[str, float]] = []
        self.retries: list[tuple[str, int, int]] = []
        self.git_recoveries: list[tuple[str, str]] = []
        self.stalls: list[tuple[str, float]] = []
        self.agent_errors: list[tuple[str, str]] = []

    def on_rate_limit(self, agent_name: str, message: str) -> None:
        self.rate_limits.append((agent_name, message))

    def on_rate_limit_wait(self, agent_name: str, wait_seconds: float) -> None:
        self.rate_limit_waits.append((agent_name, wait_seconds))

    def on_retry(self, agent_name: str, attempt: int, max_attempts: int) -> None:
        self.retries.append((agent_name, attempt, max_attempts))

    def on_git_recovery(self, action: str, detail: str) -> None:
        self.git_recoveries.append((action, detail))

    def on_stall_detected(self, agent_name: str, duration: float) -> None:
        self.stalls.append((agent_name, duration))

    def on_agent_error(self, agent_name: str, error: str) -> None:
        self.agent_errors.append((agent_name, error))


class _ShutdownRecordingCallback(_RecordingCallback):
    def __init__(self) -> None:
        super().__init__()
        self.shutdowns: list[str] = []

    def on_shutdown(self, reason: str) -> None:
        self.shutdowns.append(reason)


def _build_agent_result(
    *,
    agent_name: str,
    output_path: Path,
    exit_code: int,
    output_empty: bool,
    error: str | None = None,
    error_category: AgentErrorCategory | None = None,
    rate_limit_reset_seconds: int | None = None,
) -> AgentResult:
    return AgentResult(
        agent_name=agent_name,
        output_path=output_path,
        stream_path=output_path.with_suffix(".stream"),
        log_path=output_path.with_suffix(".log"),
        exit_code=exit_code,
        duration=timedelta(0),
        output_empty=output_empty,
        error=error,
        error_category=error_category,
        rate_limit_reset_seconds=rate_limit_reset_seconds,
    )


def _register_agents(
    registry: AgentRegistry,
    sample_agent_config: AgentConfig,
    *agent_names: str,
) -> None:
    for agent_name in agent_names:
        registry.register(sample_agent_config.model_copy(update={"name": agent_name}))


@dataclass
class _RetryTestSetup:
    """Common objects shared across rate-limit retry tests."""

    runner: AgentRunner
    phase_runner: PhaseRunner
    ui_callback: _RecordingCallback
    output_path: Path
    agent: AgentConfig


@pytest.fixture
def retry_test_setup(sample_agent_config: AgentConfig, tmp_path: Path) -> _RetryTestSetup:
    """Construct the shared runner, phase_runner, ui_callback, output_path, and agent
    used by all four rate-limit retry tests."""
    runner = AgentRunner()
    return _RetryTestSetup(
        runner=runner,
        phase_runner=PhaseRunner(runner=runner, registry=AgentRegistry()),
        ui_callback=_RecordingCallback(),
        output_path=tmp_path / "codex.md",
        agent=sample_agent_config.model_copy(update={"name": "codex"}),
    )


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


def test_phase_runner_constructor_rejects_invalid_limits() -> None:
    runner = AgentRunner()
    registry = AgentRegistry()

    with pytest.raises(ValueError, match="max_concurrency"):
        PhaseRunner(runner=runner, registry=registry, max_concurrency=0)

    with pytest.raises(ValueError, match="stall_check_interval"):
        PhaseRunner(runner=runner, registry=registry, stall_check_interval=0)


def test_install_signal_handlers_noops_without_running_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase_runner = PhaseRunner(runner=AgentRunner(), registry=AgentRegistry())

    def raise_no_loop() -> FakeLoop:
        raise RuntimeError("no running loop")

    monkeypatch.setattr(asyncio, "get_running_loop", raise_no_loop)

    phase_runner._install_signal_handlers()

    assert phase_runner._signal_handlers_installed is False


def test_install_signal_handlers_ignores_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnsupportedLoop(FakeLoop):
        def add_signal_handler(self, sig: signal.Signals, callback: Callable[[], None]) -> None:
            del sig, callback
            raise NotImplementedError

    phase_runner = PhaseRunner(runner=AgentRunner(), registry=AgentRegistry())
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: UnsupportedLoop())

    phase_runner._install_signal_handlers()

    assert phase_runner._signal_handlers_installed is False


def test_install_signal_handlers_registers_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_loop = FakeLoop()
    phase_runner = PhaseRunner(runner=AgentRunner(), registry=AgentRegistry())
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    phase_runner._install_signal_handlers()
    phase_runner._install_signal_handlers()

    assert phase_runner._signal_handlers_installed is True
    assert [sig for sig, _callback in fake_loop.added_signal_handlers] == [
        signal.SIGINT,
        signal.SIGTERM,
    ]


def test_initiate_shutdown_schedules_forced_kill_after_thirty_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    fake_loop = FakeLoop()
    runner = AgentRunner()
    registry = AgentRegistry()
    phase_runner = PhaseRunner(runner=runner, registry=registry)
    terminate_calls: list[str] = []

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)
    monkeypatch.setattr(
        phase_runner,
        "terminate_active_processes",
        lambda: terminate_calls.append("terminated"),
    )

    # Act
    phase_runner._initiate_shutdown()

    # Assert
    assert phase_runner._shutting_down is True
    assert terminate_calls == ["terminated"]
    assert len(fake_loop.scheduled_callbacks) == 1
    delay, callback = fake_loop.scheduled_callbacks[0]
    assert delay == 30.0
    assert getattr(callback, "__self__", None) is phase_runner
    assert getattr(callback, "__func__", None) is PhaseRunner._force_kill_all


def test_initiate_shutdown_second_call_forces_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase_runner = PhaseRunner(runner=AgentRunner(), registry=AgentRegistry())
    phase_runner._shutting_down = True
    force_calls: list[str] = []
    monkeypatch.setattr(phase_runner, "_force_kill_all", lambda: force_calls.append("forced"))

    phase_runner._initiate_shutdown()

    assert force_calls == ["forced"]


def test_initiate_shutdown_emits_callback_before_missing_loop_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase_runner = PhaseRunner(runner=AgentRunner(), registry=AgentRegistry())
    callback = _ShutdownRecordingCallback()
    phase_runner._ui_callback = callback

    def raise_no_loop() -> FakeLoop:
        raise RuntimeError("no running loop")

    monkeypatch.setattr(asyncio, "get_running_loop", raise_no_loop)

    phase_runner._initiate_shutdown()

    assert callback.shutdowns == ["Signal received"]


def test_force_kill_all_skips_exited_processes_and_suppresses_lookup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase_runner = PhaseRunner(runner=AgentRunner(), registry=AgentRegistry())
    live = FakeProcess()
    exited = FakeProcess(returncode=0)
    missing = FakeProcess(raise_on_kill=True)
    phase_runner._active_processes = [live, exited, missing]  # type: ignore[list-item]
    killed: list[int] = []

    def fake_kill_process_tree(proc: FakeProcess) -> None:
        proc.kill()
        killed.append(proc.pid)

    monkeypatch.setattr("orchcore.pipeline.engine.kill_process_tree", fake_kill_process_tree)

    phase_runner._force_kill_all()

    assert live.kill_calls == 1
    assert exited.kill_calls == 0
    assert missing.kill_calls == 1
    assert killed == [live.pid]


def test_terminate_active_processes_uses_tree_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase_runner = PhaseRunner(runner=AgentRunner(), registry=AgentRegistry())
    live = FakeProcess()
    exited = FakeProcess(returncode=0)
    phase_runner._active_processes = [live, exited]  # type: ignore[list-item]
    terminated: list[int] = []

    def fake_terminate_process_tree(proc: FakeProcess) -> None:
        proc.terminate()
        terminated.append(proc.pid)

    monkeypatch.setattr(
        "orchcore.pipeline.engine.terminate_process_tree",
        fake_terminate_process_tree,
    )

    phase_runner.terminate_active_processes()

    assert live.terminate_calls == 1
    assert exited.terminate_calls == 0
    assert terminated == [live.pid]


@pytest.mark.asyncio
async def test_run_phase_short_circuits_when_shutdown_or_no_agents() -> None:
    phase_runner = PhaseRunner(runner=AgentRunner(), registry=AgentRegistry())
    callback = NullCallback()
    phase_runner._shutting_down = True

    shutdown_result = await phase_runner.run_phase(
        phase=Phase(name="analysis", agents=["codex"]),
        prompt="run",
        ui_callback=callback,
        mode=AgentMode.PLAN,
    )

    phase_runner._shutting_down = False
    empty_result = await phase_runner.run_phase(
        phase=Phase(name="notes", agents=[]),
        prompt="run",
        ui_callback=callback,
        mode=AgentMode.PLAN,
    )

    assert shutdown_result.status is PhaseStatus.FAILED
    assert shutdown_result.error == "Shutdown in progress"
    assert empty_result.status is PhaseStatus.SKIPPED
    assert empty_result.error == "Phase 'notes' has no configured agents"


@pytest.mark.asyncio
async def test_run_phase_reports_unknown_agent_and_output_path_errors(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unknown_runner = PhaseRunner(runner=AgentRunner(), registry=AgentRegistry())

    unknown_result = await unknown_runner.run_phase(
        phase=Phase(name="analysis", agents=["missing"]),
        prompt="run",
        ui_callback=NullCallback(),
        mode=AgentMode.PLAN,
    )

    registry = AgentRegistry({"codex": sample_agent_config.model_copy(update={"name": "codex"})})
    path_runner = PhaseRunner(runner=AgentRunner(), registry=registry)

    async def raise_os_error(phase_name: str, agent_name: str) -> Path:
        del phase_name, agent_name
        raise OSError("disk full")

    monkeypatch.setattr(path_runner, "_resolve_output_path", raise_os_error)
    path_result = await path_runner.run_phase(
        phase=Phase(name="analysis", agents=["codex"]),
        prompt="run",
        ui_callback=NullCallback(),
        mode=AgentMode.PLAN,
    )

    assert unknown_result.status is PhaseStatus.FAILED
    assert "references unknown agent 'missing'" in (unknown_result.error or "")
    assert path_result.status is PhaseStatus.FAILED
    assert "Failed to prepare output paths" in (path_result.error or "")


@pytest.mark.asyncio
async def test_run_parallel_reports_unknown_agent_and_output_path_errors(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unknown_runner = PhaseRunner(runner=AgentRunner(), registry=AgentRegistry())

    unknown_result = await unknown_runner.run_parallel(
        phase=Phase(name="analysis", agents=["missing"], parallel=True),
        prompt="run",
        ui_callback=NullCallback(),
        mode=AgentMode.PLAN,
    )

    registry = AgentRegistry({"codex": sample_agent_config.model_copy(update={"name": "codex"})})
    path_runner = PhaseRunner(runner=AgentRunner(), registry=registry)

    async def raise_os_error(phase_name: str, agent_name: str) -> Path:
        del phase_name, agent_name
        raise OSError("disk full")

    monkeypatch.setattr(path_runner, "_resolve_output_path", raise_os_error)
    path_result = await path_runner.run_parallel(
        phase=Phase(name="analysis", agents=["codex"], parallel=True),
        prompt="run",
        ui_callback=NullCallback(),
        mode=AgentMode.PLAN,
    )

    assert unknown_result.status is PhaseStatus.FAILED
    assert "references unknown agent 'missing'" in (unknown_result.error or "")
    assert path_result.status is PhaseStatus.FAILED
    assert "Failed to prepare output paths" in (path_result.error or "")


@pytest.mark.parametrize(
    ("agent_tools", "explicit_toolset", "phase_tools", "expected_toolset"),
    [
        pytest.param(
            {"codex": ToolSet(internal=["agent-override"])},
            ToolSet(internal=["explicit"]),
            ToolSet(internal=["phase"]),
            ToolSet(internal=["agent-override"]),
            id="agent-tools-have-highest-priority",
        ),
        pytest.param(
            {},
            ToolSet(internal=["explicit"]),
            ToolSet(internal=["phase"]),
            ToolSet(internal=["explicit"]),
            id="explicit-toolset-beats-phase-tools",
        ),
        pytest.param(
            {},
            None,
            ToolSet(internal=["phase"]),
            ToolSet(internal=["phase"]),
            id="phase-tools-used-when-no-higher-priority-toolset-exists",
        ),
        pytest.param(
            {},
            None,
            None,
            None,
            id="none-delegates-to-agent-runner-defaults",
        ),
    ],
)
def test_resolve_toolset_applies_priority_order(
    agent_tools: dict[str, ToolSet],
    explicit_toolset: ToolSet | None,
    phase_tools: ToolSet | None,
    expected_toolset: ToolSet | None,
) -> None:
    # Arrange
    runner = AgentRunner()
    registry = AgentRegistry()
    phase_runner = PhaseRunner(runner=runner, registry=registry)
    phase = Phase(
        name="analysis",
        agents=["codex"],
        tools=phase_tools,
        agent_tools=agent_tools,
    )

    # Act
    resolved_toolset = phase_runner._resolve_toolset(
        phase=phase,
        agent_name="codex",
        explicit_toolset=explicit_toolset,
    )

    # Assert
    assert resolved_toolset == expected_toolset


@pytest.mark.asyncio
async def test_resolve_output_path_creates_and_reuses_fallback_workspace(
    tmp_path: Path,
) -> None:
    phase_runner = PhaseRunner(runner=AgentRunner(), registry=AgentRegistry())
    phase_runner._fallback_workspace_dir = tmp_path / ".orchcore-workspace"

    output_path = await phase_runner._resolve_output_path(
        phase_name="analysis / ../phase",
        agent_name="../codex ??",
    )
    cached_root = await phase_runner._workspace_root()

    assert output_path == (
        tmp_path / ".orchcore-workspace" / "outputs" / "analysis-..-phase" / "codex.md"
    )
    assert output_path.parent.is_dir()
    assert cached_root == tmp_path / ".orchcore-workspace"


@pytest.mark.asyncio
async def test_workspace_root_uses_injected_workspace_and_caches_result(
    tmp_path: Path,
) -> None:
    from orchcore.workspace.manager import WorkspaceManager

    workspace = WorkspaceManager(project_root=tmp_path)
    phase_runner = PhaseRunner(
        runner=AgentRunner(),
        registry=AgentRegistry(),
        workspace=workspace,
    )

    root = await phase_runner._workspace_root()
    cached_root = await phase_runner._workspace_root()

    assert root == workspace.workspace_dir
    assert cached_root == workspace.workspace_dir
    assert workspace.workspace_dir.is_dir()


def test_resolve_agent_cwd_prefers_explicit_cwd(tmp_path: Path) -> None:
    phase_runner = PhaseRunner(
        runner=AgentRunner(),
        registry=AgentRegistry(),
        agent_cwd=tmp_path,
    )

    assert phase_runner._resolve_agent_cwd() == tmp_path


def test_telemetry_span_helpers_delegate_when_telemetry_is_present() -> None:
    class FakeTelemetry:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str | None]] = []

        def phase_span(
            self,
            phase: str,
            agent: str | None = None,
        ) -> contextlib.AbstractContextManager[object]:
            self.calls.append(("phase", phase, agent))
            return contextlib.nullcontext("phase-span")

        def agent_span(
            self,
            phase: str,
            agent: str,
        ) -> contextlib.AbstractContextManager[object]:
            self.calls.append(("agent", phase, agent))
            return contextlib.nullcontext("agent-span")

    telemetry = FakeTelemetry()

    with _phase_span(telemetry, "analysis", agent="codex") as phase_value:
        assert phase_value == "phase-span"
    with _agent_span(telemetry, "analysis", "codex") as agent_value:
        assert agent_value == "agent-span"

    assert telemetry.calls == [
        ("phase", "analysis", "codex"),
        ("agent", "analysis", "codex"),
    ]


def test_phase_result_aggregation_and_helper_fallbacks(tmp_path: Path) -> None:
    success = AgentResult(
        agent_name="alpha",
        output_path=tmp_path / "alpha.md",
        exit_code=0,
        duration=timedelta(0),
        output_empty=False,
        cost_usd=Decimal("0.20"),
    )
    empty_success = AgentResult(
        agent_name="empty",
        output_path=tmp_path / "empty.md",
        exit_code=0,
        duration=timedelta(0),
        output_empty=True,
    )
    empty_with_runner_error = empty_success.model_copy(
        update={
            "error": "Agent 'empty' completed without producing output",
            "error_category": AgentErrorCategory.EMPTY_OUTPUT,
        }
    )
    exit_failure = AgentResult(
        agent_name="beta",
        output_path=tmp_path / "beta.md",
        exit_code=2,
        duration=timedelta(0),
        output_empty=False,
        cost_usd=Decimal("0.30"),
    )
    synthetic = _synthetic_agent_result(
        agent_name="gamma",
        output_path=tmp_path / "gamma.md",
        phase_name="analysis",
        error="launch failed",
        category=AgentErrorCategory.OS_ERROR,
    )

    partial = _build_phase_result(
        phase_name="analysis",
        started_at=datetime.now(UTC),
        agent_results=[success, exit_failure],
        allow_partial=True,
    )
    empty_failure = _build_phase_result(
        phase_name="analysis",
        started_at=datetime.now(UTC),
        agent_results=[empty_with_runner_error],
        allow_partial=False,
    )

    assert partial.status is PhaseStatus.PARTIAL
    assert partial.output_files == [tmp_path / "alpha.md", tmp_path / "beta.md"]
    assert partial.error == "Agent 'beta' exited with code 2"
    assert partial.cost_usd == Decimal("0.50")
    assert _agent_error_message(success) is None
    assert _agent_error_message(empty_success) == "Agent 'empty' completed without producing output"
    assert _agent_error_message(empty_with_runner_error) == (
        "Agent 'empty' completed without producing output"
    )
    assert _agent_error_message(exit_failure) == "Agent 'beta' exited with code 2"
    assert synthetic.error == "launch failed (phase='analysis', agent='gamma')"
    assert synthetic.error_category is AgentErrorCategory.OS_ERROR
    assert partial.error_messages == ["Agent 'beta' exited with code 2"]
    assert empty_failure.error_messages == ["Agent 'empty' completed without producing output"]
    assert _exception_message(RuntimeError()) == "RuntimeError"
    assert _path_component(" !!! ") == "unnamed"


@pytest.mark.asyncio
async def test_restore_git_stash_failure_reports_callback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingRestoreGitRecovery(GitRecovery):
        async def restore_stash(self) -> bool:
            return False

    callback = _RecordingCallback()

    with caplog.at_level("WARNING", logger="orchcore.pipeline.engine"):
        await _restore_git_stash_if_needed(FailingRestoreGitRecovery(), callback)

    assert "Git stash restore failed after retry wait" in caplog.text
    assert callback.git_recoveries == [
        ("stash_restore_failed", "git stash restore failed after retry wait")
    ]


@pytest.mark.asyncio
async def test_run_with_semaphore_retries_rate_limit_then_succeeds(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    setup = retry_test_setup
    attempts = 0
    wait_inputs: list[tuple[int, int | None]] = []
    sleep_calls: list[float] = []

    async def fake_run(**_: object) -> AgentResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _build_agent_result(
                agent_name="codex",
                output_path=setup.output_path,
                exit_code=1,
                output_empty=True,
                error="429 rate limit exceeded, try again in 17 seconds",
                error_category=AgentErrorCategory.RATE_LIMIT,
                rate_limit_reset_seconds=17,
            )
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=0,
            output_empty=False,
        )

    async def fake_is_tree_dirty(self: object) -> bool:
        del self
        return False

    async def fake_auto_commit(self: object, *, no_verify: bool = False) -> bool:
        del self, no_verify
        return False

    def fake_compute_wait(self: object, attempt: int, reset_seconds: int | None = None) -> float:
        del self
        wait_inputs.append((attempt, reset_seconds))
        return 0.25

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(setup.runner, "run", fake_run)
    monkeypatch.setattr("orchcore.pipeline.engine.GitRecovery.is_tree_dirty", fake_is_tree_dirty)
    monkeypatch.setattr("orchcore.pipeline.engine.GitRecovery.auto_commit", fake_auto_commit)
    monkeypatch.setattr(
        "orchcore.pipeline.engine.BackoffStrategy.compute_wait",
        fake_compute_wait,
    )
    monkeypatch.setattr("orchcore.pipeline.engine.asyncio.sleep", fake_sleep)

    # Act
    result = await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="retry",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
        retry_policy=RetryPolicy(max_retries=2, failure_mode=FailureMode.FAIL_FAST),
    )

    # Assert
    assert attempts == 2
    assert result.exit_code == 0
    assert result.error is None
    assert wait_inputs == [(1, 17)]
    assert sleep_calls == [0.25]
    assert setup.ui_callback.rate_limits == [
        ("codex", "429 rate limit exceeded, try again in 17 seconds")
    ]
    assert setup.ui_callback.rate_limit_waits == [("codex", 0.25)]
    assert setup.ui_callback.retries == [("codex", 1, 2)]
    assert setup.ui_callback.git_recoveries == []


@pytest.mark.asyncio
async def test_run_with_semaphore_passes_typed_rate_limit_reset_to_backoff(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = retry_test_setup
    marker_path = setup.output_path.with_suffix(".marker")
    script = textwrap.dedent(
        f"""
        import json
        from pathlib import Path

        marker = Path({str(marker_path)!r})
        if not marker.exists():
            marker.write_text("rate-limited", encoding="utf-8")
            print(json.dumps({{
                "type": "system",
                "subtype": "rate_limit",
                "retry_after_ms": 5000,
            }}), flush=True)
        else:
            print(json.dumps({{
                "type": "assistant",
                "message": {{"content": [{{"type": "text", "text": "ok"}}]}},
            }}), flush=True)
            print(json.dumps({{"type": "result", "exit_code": 0}}), flush=True)
        """
    ).strip()
    agent = setup.agent.model_copy(
        update={
            "binary": sys.executable,
            "subcommand": "-c",
            "flags": {AgentMode.PLAN: []},
            "version_command": (),
        }
    )
    wait_inputs: list[tuple[int, int | None]] = []
    sleep_calls: list[float] = []

    async def fake_is_tree_dirty(self: object) -> bool:
        del self
        return False

    async def fake_auto_commit(self: object, *, no_verify: bool = False) -> bool:
        del self, no_verify
        return False

    def fake_compute_wait(self: object, attempt: int, reset_seconds: int | None = None) -> float:
        del self
        wait_inputs.append((attempt, reset_seconds))
        return 0.0

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr("orchcore.pipeline.engine.GitRecovery.is_tree_dirty", fake_is_tree_dirty)
    monkeypatch.setattr("orchcore.pipeline.engine.GitRecovery.auto_commit", fake_auto_commit)
    monkeypatch.setattr(
        "orchcore.pipeline.engine.BackoffStrategy.compute_wait",
        fake_compute_wait,
    )
    monkeypatch.setattr("orchcore.pipeline.engine.asyncio.sleep", fake_sleep)

    result = await setup.phase_runner._run_with_semaphore(
        agent=agent,
        prompt=script,
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
        retry_policy=RetryPolicy(max_retries=2, failure_mode=FailureMode.FAIL_FAST),
    )

    assert result.exit_code == 0
    assert result.error is None
    assert wait_inputs == [(1, 5)]
    assert sleep_calls == [0.0]
    assert setup.output_path.read_text(encoding="utf-8") == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised_error", "expected_message", "expected_category"),
    [
        pytest.param(
            FileNotFoundError("missing binary"),
            "binary",
            AgentErrorCategory.BINARY_NOT_FOUND,
            id="missing-binary",
        ),
        pytest.param(
            OSError("permission denied"),
            "failed in phase",
            AgentErrorCategory.OS_ERROR,
            id="os-error",
        ),
    ],
)
async def test_run_with_semaphore_returns_synthetic_launch_errors(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
    raised_error: OSError,
    expected_message: str,
    expected_category: AgentErrorCategory,
) -> None:
    setup = retry_test_setup

    async def fake_run(**_: object) -> AgentResult:
        raise raised_error

    monkeypatch.setattr(setup.runner, "run", fake_run)

    result = await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="run",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
    )

    assert result.exit_code == 1
    assert result.output_empty is True
    assert result.error is not None
    assert expected_message in result.error
    assert "phase='analysis', agent='codex'" in result.error
    assert result.error_category is expected_category


@pytest.mark.asyncio
async def test_run_with_semaphore_returns_immediately_when_retry_policy_disallows_retry(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = retry_test_setup
    attempts = 0

    async def fake_run(**_: object) -> AgentResult:
        nonlocal attempts
        attempts += 1
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=1,
            output_empty=True,
            error="429 rate limit exceeded",
        )

    monkeypatch.setattr(setup.runner, "run", fake_run)

    result = await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="run",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
        retry_policy=RetryPolicy(max_retries=0, failure_mode=FailureMode.FAIL_FAST),
    )

    assert attempts == 1
    assert result.error == "429 rate limit exceeded"
    assert setup.ui_callback.rate_limits == []
    assert setup.ui_callback.retries == []


@pytest.mark.asyncio
async def test_run_with_semaphore_uses_fallback_rate_limit_message(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = retry_test_setup
    attempts = 0

    async def fake_run(**_: object) -> AgentResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _build_agent_result(
                agent_name="codex",
                output_path=setup.output_path,
                exit_code=1,
                output_empty=True,
                error="   ",
                error_category=AgentErrorCategory.RATE_LIMIT,
            )
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=0,
            output_empty=False,
        )

    monkeypatch.setattr(setup.runner, "run", fake_run)
    monkeypatch.setattr(
        "orchcore.pipeline.engine.BackoffStrategy.compute_wait",
        lambda *_args, **_kwargs: 0.0,
    )
    monkeypatch.setattr("orchcore.pipeline.engine.asyncio.sleep", _async_noop)

    result = await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="run",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
        retry_policy=RetryPolicy(max_retries=1, failure_mode=FailureMode.FAIL_FAST),
    )

    assert result.exit_code == 0
    assert setup.ui_callback.rate_limits == [("codex", "Agent 'codex' hit a rate limit")]


@pytest.mark.asyncio
async def test_run_with_semaphore_passes_explicit_stall_callback(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = retry_test_setup
    captured_on_stall: object | None = None

    async def fake_run(**kwargs: object) -> AgentResult:
        nonlocal captured_on_stall
        captured_on_stall = kwargs["on_stall"]
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=0,
            output_empty=False,
        )

    monkeypatch.setattr(setup.runner, "run", fake_run)

    await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="run",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
    )

    assert callable(captured_on_stall)
    assert getattr(captured_on_stall, "__self__", None) is setup.ui_callback
    assert getattr(captured_on_stall, "__func__", None) is _RecordingCallback.on_stall_detected


@pytest.mark.asyncio
async def test_run_with_semaphore_avoids_legacy_stall_callback_resolution(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = retry_test_setup
    script = textwrap.dedent(
        """
        import json

        print(json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1"}), flush=True)
        print(json.dumps({"type": "result", "session_id": "sess-1"}), flush=True)
        """
    ).strip()
    agent = setup.agent.model_copy(
        update={
            "binary": sys.executable,
            "subcommand": "-c",
            "flags": {AgentMode.PLAN: []},
        }
    )

    def fail_legacy_resolution(on_event: object) -> None:
        del on_event
        raise AssertionError("legacy stall callback resolver should not run")

    monkeypatch.setattr(
        "orchcore.runner.subprocess._resolve_stall_callback",
        fail_legacy_resolution,
    )

    result = await setup.phase_runner._run_with_semaphore(
        agent=agent,
        prompt=script,
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
    )

    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_run_with_semaphore_does_not_retry_non_rate_limit_failures(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    setup = retry_test_setup
    attempts = 0
    sleep_calls: list[float] = []

    async def fake_run(**_: object) -> AgentResult:
        nonlocal attempts
        attempts += 1
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=1,
            output_empty=True,
            error="internal failure",
        )

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(setup.runner, "run", fake_run)
    monkeypatch.setattr("orchcore.pipeline.engine.asyncio.sleep", fake_sleep)

    # Act
    result = await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="retry",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
        retry_policy=RetryPolicy(max_retries=3, failure_mode=FailureMode.FAIL_FAST),
    )

    # Assert
    assert attempts == 1
    assert result.exit_code == 1
    assert result.error == "internal failure"
    assert sleep_calls == []
    assert setup.ui_callback.rate_limits == []
    assert setup.ui_callback.rate_limit_waits == []
    assert setup.ui_callback.retries == []
    assert setup.ui_callback.git_recoveries == []


@pytest.mark.asyncio
async def test_run_with_semaphore_emits_git_recovery_callback_before_retry(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    setup = retry_test_setup
    attempts = 0

    async def fake_run(**_: object) -> AgentResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _build_agent_result(
                agent_name="codex",
                output_path=setup.output_path,
                exit_code=1,
                output_empty=True,
                error="rate limit exceeded",
                error_category=AgentErrorCategory.RATE_LIMIT,
            )
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=0,
            output_empty=False,
        )

    async def fake_is_tree_dirty(self: object) -> bool:
        del self
        return True

    async def fake_auto_commit(self: object, *, no_verify: bool = False) -> bool:
        del self, no_verify
        return True

    def fake_compute_wait(self: object, attempt: int, reset_seconds: int | None = None) -> float:
        del self, attempt, reset_seconds
        return 0.0

    async def fake_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr(setup.runner, "run", fake_run)
    monkeypatch.setattr("orchcore.pipeline.engine.GitRecovery.is_tree_dirty", fake_is_tree_dirty)
    monkeypatch.setattr("orchcore.pipeline.engine.GitRecovery.auto_commit", fake_auto_commit)
    monkeypatch.setattr(
        "orchcore.pipeline.engine.BackoffStrategy.compute_wait",
        fake_compute_wait,
    )
    monkeypatch.setattr("orchcore.pipeline.engine.asyncio.sleep", fake_sleep)

    # Act
    result = await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="retry",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
        retry_policy=RetryPolicy(
            max_retries=2,
            failure_mode=FailureMode.FAIL_FAST,
            git_recovery="auto_commit",
            git_recovery_cwd=setup.output_path.parent,
        ),
    )

    # Assert
    assert attempts == 2
    assert result.exit_code == 0
    assert setup.ui_callback.git_recoveries == [("auto_commit", "cleaned dirty tree before retry")]


@pytest.mark.asyncio
async def test_run_with_semaphore_default_git_recovery_runs_no_git_commands(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = retry_test_setup
    attempts = 0

    async def fake_run(**_: object) -> AgentResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _build_agent_result(
                agent_name="codex",
                output_path=setup.output_path,
                exit_code=1,
                output_empty=True,
                error="rate limit exceeded",
                error_category=AgentErrorCategory.RATE_LIMIT,
            )
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=0,
            output_empty=False,
        )

    class FailingGitRecovery:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("GitRecovery must not be constructed by default")

    monkeypatch.setattr(setup.runner, "run", fake_run)
    monkeypatch.setattr("orchcore.pipeline.engine.GitRecovery", FailingGitRecovery)
    monkeypatch.setattr(
        "orchcore.pipeline.engine.BackoffStrategy.compute_wait",
        lambda *_args, **_kwargs: 0.0,
    )
    monkeypatch.setattr("orchcore.pipeline.engine.asyncio.sleep", _async_noop)

    result = await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="retry",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
        retry_policy=RetryPolicy(max_retries=2, failure_mode=FailureMode.FAIL_FAST),
    )

    assert attempts == 2
    assert result.exit_code == 0
    assert setup.ui_callback.git_recoveries == []


@pytest.mark.asyncio
async def test_run_with_semaphore_retries_exit_zero_stream_rate_limit(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = retry_test_setup
    attempts = 0

    async def fake_run(**_: object) -> AgentResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _build_agent_result(
                agent_name="codex",
                output_path=setup.output_path,
                exit_code=0,
                output_empty=False,
                error="agent reported a rate limit",
                error_category=AgentErrorCategory.RATE_LIMIT,
            )
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=0,
            output_empty=False,
        )

    monkeypatch.setattr(setup.runner, "run", fake_run)
    monkeypatch.setattr(
        "orchcore.pipeline.engine.BackoffStrategy.compute_wait",
        lambda *_args, **_kwargs: 0.0,
    )
    monkeypatch.setattr("orchcore.pipeline.engine.asyncio.sleep", _async_noop)

    result = await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="retry",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
        retry_policy=RetryPolicy(max_retries=2, failure_mode=FailureMode.FAIL_FAST),
    )

    assert attempts == 2
    assert result.error is None
    assert setup.ui_callback.rate_limits == [("codex", "agent reported a rate limit")]


@pytest.mark.asyncio
async def test_run_with_semaphore_retries_on_category_without_string_matching(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WP-18: the retry decision keys on error_category alone — an error
    string that matches no RateLimitDetector pattern still retries."""
    setup = retry_test_setup
    attempts = 0
    wait_inputs: list[tuple[int, int | None]] = []

    async def fake_run(**_: object) -> AgentResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _build_agent_result(
                agent_name="codex",
                output_path=setup.output_path,
                exit_code=1,
                output_empty=True,
                error="provider capacity replenishes shortly",
                error_category=AgentErrorCategory.RATE_LIMIT,
                rate_limit_reset_seconds=42,
            )
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=0,
            output_empty=False,
        )

    def fake_compute_wait(self: object, attempt: int, reset_seconds: int | None = None) -> float:
        del self
        wait_inputs.append((attempt, reset_seconds))
        return 0.0

    monkeypatch.setattr(setup.runner, "run", fake_run)
    monkeypatch.setattr(
        "orchcore.pipeline.engine.BackoffStrategy.compute_wait",
        fake_compute_wait,
    )
    monkeypatch.setattr("orchcore.pipeline.engine.asyncio.sleep", _async_noop)

    result = await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="retry",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
        retry_policy=RetryPolicy(max_retries=2, failure_mode=FailureMode.FAIL_FAST),
    )

    assert attempts == 2
    assert result.error is None
    assert wait_inputs == [(1, 42)]
    assert setup.ui_callback.rate_limits == [("codex", "provider capacity replenishes shortly")]


@pytest.mark.asyncio
async def test_run_with_semaphore_does_not_retry_rate_limit_prose_without_category(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WP-18: stdout noise that merely looks like a rate limit (no typed
    category) no longer triggers a retry at the engine level."""
    setup = retry_test_setup
    attempts = 0

    async def fake_run(**_: object) -> AgentResult:
        nonlocal attempts
        attempts += 1
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=1,
            output_empty=True,
            error="429 rate limit exceeded",
            error_category=AgentErrorCategory.NONZERO_EXIT,
        )

    monkeypatch.setattr(setup.runner, "run", fake_run)

    result = await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="run",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
        retry_policy=RetryPolicy(max_retries=3, failure_mode=FailureMode.FAIL_FAST),
    )

    assert attempts == 1
    assert result.error == "429 rate limit exceeded"
    assert setup.ui_callback.rate_limits == []
    assert setup.ui_callback.retries == []


@pytest.mark.asyncio
async def test_run_with_semaphore_passes_workspace_project_root_as_cwd(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from orchcore.workspace.manager import WorkspaceManager

    captured_cwd: Path | None = None
    runner = AgentRunner()
    phase_runner = PhaseRunner(
        runner=runner,
        registry=AgentRegistry(),
        workspace=WorkspaceManager(project_root=tmp_path),
    )

    async def fake_run(**kwargs: object) -> AgentResult:
        nonlocal captured_cwd
        captured_cwd = kwargs["cwd"]  # type: ignore[assignment]
        return _build_agent_result(
            agent_name="codex",
            output_path=tmp_path / "codex.md",
            exit_code=0,
            output_empty=False,
        )

    monkeypatch.setattr(runner, "run", fake_run)

    await phase_runner._run_with_semaphore(
        agent=sample_agent_config.model_copy(update={"name": "codex"}),
        prompt="run",
        output_path=tmp_path / "codex.md",
        phase_name="analysis",
        ui_callback=_RecordingCallback(),
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
    )

    assert captured_cwd == tmp_path


@pytest.mark.asyncio
async def test_run_with_semaphore_leaves_cwd_none_without_workspace_or_override(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = retry_test_setup
    captured_cwd: object = "unset"

    async def fake_run(**kwargs: object) -> AgentResult:
        nonlocal captured_cwd
        captured_cwd = kwargs["cwd"]
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=0,
            output_empty=False,
        )

    monkeypatch.setattr(setup.runner, "run", fake_run)

    await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="run",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
    )

    assert captured_cwd is None


@pytest.mark.asyncio
async def test_run_with_semaphore_uses_policy_git_recovery_cwd(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    setup = retry_test_setup
    attempts = 0
    constructed_working_dirs: list[str | None] = []
    no_verify_values: list[bool] = []
    recovery_cwd = tmp_path / "repo"

    async def fake_run(**_: object) -> AgentResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _build_agent_result(
                agent_name="codex",
                output_path=setup.output_path,
                exit_code=1,
                output_empty=True,
                error="rate limit exceeded",
                error_category=AgentErrorCategory.RATE_LIMIT,
            )
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=0,
            output_empty=False,
        )

    class FakeGitRecovery:
        def __init__(self, working_dir: str | None = None) -> None:
            constructed_working_dirs.append(working_dir)

        async def is_tree_dirty(self) -> bool:
            return True

        async def auto_commit(self, message: str | None = None, *, no_verify: bool = False) -> bool:
            del message
            no_verify_values.append(no_verify)
            return True

    monkeypatch.setattr(setup.runner, "run", fake_run)
    monkeypatch.setattr("orchcore.pipeline.engine.GitRecovery", FakeGitRecovery)
    monkeypatch.setattr(
        "orchcore.pipeline.engine.BackoffStrategy.compute_wait",
        lambda *_args, **_kwargs: 0.0,
    )
    monkeypatch.setattr("orchcore.pipeline.engine.asyncio.sleep", _async_noop)

    await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="retry",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
        retry_policy=RetryPolicy(
            max_retries=2,
            failure_mode=FailureMode.FAIL_FAST,
            git_recovery="auto_commit",
            git_recovery_cwd=recovery_cwd,
            git_recovery_no_verify=True,
        ),
    )

    assert constructed_working_dirs == [str(recovery_cwd)]
    assert no_verify_values == [True]
    assert setup.ui_callback.git_recoveries == [("auto_commit", "cleaned dirty tree before retry")]


@pytest.mark.asyncio
async def test_run_with_semaphore_stashes_and_restores_before_retry(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    setup = retry_test_setup
    attempts = 0
    calls: list[str] = []

    async def fake_run(**_: object) -> AgentResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _build_agent_result(
                agent_name="codex",
                output_path=setup.output_path,
                exit_code=1,
                output_empty=True,
                error="rate limit exceeded",
                error_category=AgentErrorCategory.RATE_LIMIT,
            )
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=0,
            output_empty=False,
        )

    class FakeGitRecovery:
        def __init__(self, working_dir: str | None = None) -> None:
            calls.append(f"cwd:{working_dir}")

        async def stash_dirty_tree(self) -> bool:
            calls.append("stash")
            return True

        async def restore_stash(self) -> bool:
            calls.append("restore")
            return True

    monkeypatch.setattr(setup.runner, "run", fake_run)
    monkeypatch.setattr("orchcore.pipeline.engine.GitRecovery", FakeGitRecovery)
    monkeypatch.setattr(
        "orchcore.pipeline.engine.BackoffStrategy.compute_wait",
        lambda *_args, **_kwargs: 0.0,
    )
    monkeypatch.setattr("orchcore.pipeline.engine.asyncio.sleep", _async_noop)

    await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="retry",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
        retry_policy=RetryPolicy(
            max_retries=2,
            failure_mode=FailureMode.FAIL_FAST,
            git_recovery="stash",
            git_recovery_cwd=tmp_path,
        ),
    )

    assert calls == [f"cwd:{tmp_path}", "stash", "restore"]
    assert setup.ui_callback.git_recoveries == [
        ("stash", "stashed dirty tree before retry"),
        ("stash_restore", "restored dirty tree after retry wait"),
    ]


@pytest.mark.asyncio
async def test_run_with_semaphore_restores_stash_when_shutdown_happens_before_sleep(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    setup = retry_test_setup
    calls: list[str] = []

    async def fake_run(**_: object) -> AgentResult:
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=1,
            output_empty=True,
            error="rate limit exceeded",
            error_category=AgentErrorCategory.RATE_LIMIT,
        )

    class FakeGitRecovery:
        def __init__(self, working_dir: str | None = None) -> None:
            calls.append(f"cwd:{working_dir}")

        async def stash_dirty_tree(self) -> bool:
            calls.append("stash")
            return True

        async def restore_stash(self) -> bool:
            calls.append("restore")
            return True

    def fake_compute_wait(self: object, attempt: int, reset_seconds: int | None = None) -> float:
        del self, attempt, reset_seconds
        setup.phase_runner._shutting_down = True
        return 0.0

    async def fail_sleep(delay: float) -> None:
        del delay
        raise AssertionError("shutdown should skip retry sleep")

    monkeypatch.setattr(setup.runner, "run", fake_run)
    monkeypatch.setattr("orchcore.pipeline.engine.GitRecovery", FakeGitRecovery)
    monkeypatch.setattr(
        "orchcore.pipeline.engine.BackoffStrategy.compute_wait",
        fake_compute_wait,
    )
    monkeypatch.setattr("orchcore.pipeline.engine.asyncio.sleep", fail_sleep)

    result = await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="retry",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
        retry_policy=RetryPolicy(
            max_retries=2,
            failure_mode=FailureMode.FAIL_FAST,
            git_recovery="stash",
            git_recovery_cwd=tmp_path,
        ),
    )

    assert result.error == "rate limit exceeded"
    assert calls == [f"cwd:{tmp_path}", "stash", "restore"]
    assert setup.ui_callback.git_recoveries == [
        ("stash", "stashed dirty tree before retry"),
        ("stash_restore", "restored dirty tree after retry wait"),
    ]


@pytest.mark.asyncio
async def test_run_with_semaphore_skips_git_recovery_without_cwd(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    setup = retry_test_setup
    attempts = 0

    async def fake_run(**_: object) -> AgentResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _build_agent_result(
                agent_name="codex",
                output_path=setup.output_path,
                exit_code=1,
                output_empty=True,
                error="rate limit exceeded",
                error_category=AgentErrorCategory.RATE_LIMIT,
            )
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=0,
            output_empty=False,
        )

    class FailingGitRecovery:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("GitRecovery must not be constructed without a cwd")

    monkeypatch.setattr(setup.runner, "run", fake_run)
    monkeypatch.setattr("orchcore.pipeline.engine.GitRecovery", FailingGitRecovery)
    monkeypatch.setattr(
        "orchcore.pipeline.engine.BackoffStrategy.compute_wait",
        lambda *_args, **_kwargs: 0.0,
    )
    monkeypatch.setattr("orchcore.pipeline.engine.asyncio.sleep", _async_noop)

    with caplog.at_level("WARNING", logger="orchcore.pipeline.engine"):
        await setup.phase_runner._run_with_semaphore(
            agent=setup.agent,
            prompt="retry",
            output_path=setup.output_path,
            phase_name="analysis",
            ui_callback=setup.ui_callback,
            mode=AgentMode.PLAN,
            phase_failure_mode=FailureMode.FAIL_FAST,
            retry_policy=RetryPolicy(
                max_retries=2,
                failure_mode=FailureMode.FAIL_FAST,
                git_recovery="auto_commit",
            ),
        )

    assert "Skipping git recovery mode" in caplog.text
    assert setup.ui_callback.git_recoveries == [
        ("skipped_no_cwd", "git recovery skipped because no cwd was resolved")
    ]


@pytest.mark.asyncio
async def test_run_phase_treats_exit_zero_agent_error_as_failed_phase(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """F1 regression: an exit-0 stream error aggregates to a failed phase."""
    registry = AgentRegistry()
    registry.register(sample_agent_config.model_copy(update={"name": "codex"}))
    runner = AgentRunner()
    phase_runner = PhaseRunner(runner=runner, registry=registry)
    ui_callback = _RecordingCallback()

    async def fake_run(**kwargs: object) -> AgentResult:
        return _build_agent_result(
            agent_name="codex",
            output_path=kwargs["output_path"],  # type: ignore[arg-type]
            exit_code=0,
            output_empty=False,
            error="structured failure",
        )

    async def fake_resolve_output_path(phase_name: str, agent_name: str) -> Path:
        del phase_name
        return tmp_path / f"{agent_name}.md"

    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(phase_runner, "_resolve_output_path", fake_resolve_output_path)

    result = await phase_runner.run_phase(
        phase=Phase(name="analysis", agents=["codex"]),
        prompt="run",
        ui_callback=ui_callback,
        mode=AgentMode.PLAN,
    )

    assert result.status is PhaseStatus.FAILED
    assert result.error == "structured failure"
    assert ui_callback.agent_errors == [("codex", "structured failure")]


async def _async_noop(_delay: float) -> None:
    return None


@pytest.mark.asyncio
async def test_run_parallel_cancels_pending_agents_in_fail_fast_mode(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Arrange
    registry = AgentRegistry()
    _register_agents(registry, sample_agent_config, "fast", "slow")
    phase_runner = PhaseRunner(runner=AgentRunner(), registry=registry)
    slow_started = asyncio.Event()

    async def fake_resolve_output_path(phase_name: str, agent_name: str) -> Path:
        del phase_name
        return tmp_path / f"{agent_name}.md"

    async def fake_run_with_semaphore(
        *,
        agent: AgentConfig,
        output_path: Path,
        **_: object,
    ) -> AgentResult:
        if agent.name == "fast":
            await slow_started.wait()
            return _build_agent_result(
                agent_name="fast",
                output_path=output_path,
                exit_code=1,
                output_empty=True,
                error="fast failed",
            )

        slow_started.set()
        await asyncio.sleep(10)
        return _build_agent_result(
            agent_name="slow",
            output_path=output_path,
            exit_code=0,
            output_empty=False,
        )

    monkeypatch.setattr(phase_runner, "_resolve_output_path", fake_resolve_output_path)
    monkeypatch.setattr(phase_runner, "_run_with_semaphore", fake_run_with_semaphore)

    # Act
    result = await phase_runner.run_parallel(
        phase=Phase(
            name="analysis",
            agents=["fast", "slow"],
            failure_mode=FailureMode.FAIL_FAST,
        ),
        prompt="run",
        ui_callback=NullCallback(),
        mode=AgentMode.PLAN,
    )

    # Assert
    assert result.status is PhaseStatus.FAILED
    assert [agent_result.agent_name for agent_result in result.agent_results] == ["fast", "slow"]
    assert result.agent_results[0].error == "fast failed"
    assert result.agent_results[1].error is not None
    assert "Cancelled due to fail-fast sibling failure" in result.agent_results[1].error
    assert result.agent_results[1].error_category is AgentErrorCategory.CANCELLED
    # error_messages preserves agent order alongside the joined display form.
    assert len(result.error_messages) == 2
    assert result.error_messages[0] == "fast failed"
    assert "Cancelled due to fail-fast sibling failure" in result.error_messages[1]
    assert result.error == "; ".join(result.error_messages)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_mode", "min_count", "agent_names", "failing_agents", "expected_status"),
    [
        pytest.param(
            FailureMode.CONTINUE,
            1,
            ["alpha", "beta"],
            {"beta"},
            PhaseStatus.PARTIAL,
            id="continue-partial",
        ),
        pytest.param(
            FailureMode.CONTINUE,
            1,
            ["alpha", "beta"],
            {"alpha", "beta"},
            PhaseStatus.FAILED,
            id="continue-failed",
        ),
        pytest.param(
            FailureMode.REQUIRE_MINIMUM,
            2,
            ["alpha", "beta", "gamma"],
            {"gamma"},
            PhaseStatus.PARTIAL,
            id="require-minimum-partial",
        ),
        pytest.param(
            FailureMode.REQUIRE_MINIMUM,
            2,
            ["alpha", "beta"],
            {"beta"},
            PhaseStatus.FAILED,
            id="require-minimum-failed",
        ),
    ],
)
async def test_run_parallel_evaluates_continue_and_require_minimum_statuses(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_mode: FailureMode,
    min_count: int,
    agent_names: list[str],
    failing_agents: set[str],
    expected_status: PhaseStatus,
) -> None:
    # Arrange
    registry = AgentRegistry()
    _register_agents(registry, sample_agent_config, *agent_names)
    phase_runner = PhaseRunner(runner=AgentRunner(), registry=registry)

    async def fake_resolve_output_path(phase_name: str, agent_name: str) -> Path:
        del phase_name
        return tmp_path / f"{agent_name}.md"

    async def fake_run_with_semaphore(
        *,
        agent: AgentConfig,
        output_path: Path,
        **_: object,
    ) -> AgentResult:
        if agent.name in failing_agents:
            return _build_agent_result(
                agent_name=agent.name,
                output_path=output_path,
                exit_code=1,
                output_empty=True,
                error=f"{agent.name} failed",
            )
        return _build_agent_result(
            agent_name=agent.name,
            output_path=output_path,
            exit_code=0,
            output_empty=False,
        )

    monkeypatch.setattr(phase_runner, "_resolve_output_path", fake_resolve_output_path)
    monkeypatch.setattr(phase_runner, "_run_with_semaphore", fake_run_with_semaphore)

    # Act
    result = await phase_runner.run_parallel(
        phase=Phase(
            name="analysis",
            agents=agent_names,
            failure_mode=failure_mode,
            retry_policy=RetryPolicy(failure_mode=failure_mode, min_count=min_count),
        ),
        prompt="run",
        ui_callback=NullCallback(),
        mode=AgentMode.PLAN,
    )

    # Assert
    assert result.status is expected_status


@pytest.mark.asyncio
async def test_run_phase_aborts_remaining_agents_on_shutdown(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Arrange
    registry = AgentRegistry()
    _register_agents(registry, sample_agent_config, "agent-one", "agent-two")
    phase_runner = PhaseRunner(runner=AgentRunner(), registry=registry)
    output_path_one = tmp_path / "agent-one.md"
    output_path_two = tmp_path / "agent-two.md"

    async def fake_resolve_output_path(phase_name: str, agent_name: str) -> Path:
        del phase_name
        return tmp_path / f"{agent_name}.md"

    async def fake_run_with_semaphore(
        *,
        agent: AgentConfig,
        output_path: Path,
        **_: object,
    ) -> AgentResult:
        # Trigger shutdown during the first agent's execution
        phase_runner._shutting_down = True
        return _build_agent_result(
            agent_name=agent.name,
            output_path=output_path,
            exit_code=0,
            output_empty=False,
        )

    monkeypatch.setattr(phase_runner, "_resolve_output_path", fake_resolve_output_path)
    monkeypatch.setattr(phase_runner, "_run_with_semaphore", fake_run_with_semaphore)

    # Act
    result = await phase_runner.run_phase(
        phase=Phase(name="analysis", agents=["agent-one", "agent-two"]),
        prompt="run",
        ui_callback=NullCallback(),
        mode=AgentMode.PLAN,
    )

    # Assert — only agent-one ran; agent-two was skipped because shutdown was set
    assert len(result.agent_results) == 1
    assert result.agent_results[0].agent_name == "agent-one"
    assert result.agent_results[0].output_path == output_path_one
    _ = output_path_two  # agent-two was never started


@pytest.mark.asyncio
async def test_run_with_semaphore_aborts_on_shutdown_before_retry(
    retry_test_setup: _RetryTestSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    setup = retry_test_setup
    attempts = 0
    sleep_calls: list[float] = []

    async def fake_run(**_: object) -> AgentResult:
        nonlocal attempts
        attempts += 1
        return _build_agent_result(
            agent_name="codex",
            output_path=setup.output_path,
            exit_code=1,
            output_empty=True,
            error="rate limit exceeded",
            error_category=AgentErrorCategory.RATE_LIMIT,
        )

    async def fake_is_tree_dirty(self: object) -> bool:
        del self
        return False

    async def fake_auto_commit(self: object, *, no_verify: bool = False) -> bool:
        del self, no_verify
        return False

    def fake_compute_wait(self: object, attempt: int, reset_seconds: int | None = None) -> float:
        del self, attempt, reset_seconds
        # Set shutdown flag so the retry is skipped
        setup.phase_runner._shutting_down = True
        return 0.25

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(setup.runner, "run", fake_run)
    monkeypatch.setattr("orchcore.pipeline.engine.GitRecovery.is_tree_dirty", fake_is_tree_dirty)
    monkeypatch.setattr("orchcore.pipeline.engine.GitRecovery.auto_commit", fake_auto_commit)
    monkeypatch.setattr(
        "orchcore.pipeline.engine.BackoffStrategy.compute_wait",
        fake_compute_wait,
    )
    monkeypatch.setattr("orchcore.pipeline.engine.asyncio.sleep", fake_sleep)

    # Act
    result = await setup.phase_runner._run_with_semaphore(
        agent=setup.agent,
        prompt="retry",
        output_path=setup.output_path,
        phase_name="analysis",
        ui_callback=setup.ui_callback,
        mode=AgentMode.PLAN,
        phase_failure_mode=FailureMode.FAIL_FAST,
        retry_policy=RetryPolicy(max_retries=3, failure_mode=FailureMode.FAIL_FAST),
    )

    # Assert — rate-limited result is returned immediately without sleeping or retrying
    assert attempts == 1
    assert result.exit_code == 1
    assert result.error == "rate limit exceeded"
    assert sleep_calls == []
    assert setup.ui_callback.retries == [("codex", 1, 3)]
