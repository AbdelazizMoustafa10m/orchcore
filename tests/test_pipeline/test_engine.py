from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from orchcore.pipeline.engine import PhaseRunner, _failed_phase, _skipped_phase
from orchcore.pipeline.phase import Phase, PhaseResult, PhaseStatus
from orchcore.recovery import FailureMode, RetryPolicy
from orchcore.registry.agent import AgentConfig, AgentMode, ToolSet
from orchcore.registry.registry import AgentRegistry
from orchcore.runner.subprocess import AgentRunner
from orchcore.stream.events import AgentResult
from orchcore.ui.callback import NullCallback

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class FakeLoop:
    def __init__(self) -> None:
        self.scheduled_callbacks: list[tuple[float, Callable[[], None]]] = []

    def call_later(self, delay: float, callback: Callable[[], None]) -> None:
        self.scheduled_callbacks.append((delay, callback))


class _RecordingCallback(NullCallback):
    def __init__(self) -> None:
        self.rate_limits: list[tuple[str, str]] = []
        self.rate_limit_waits: list[tuple[str, float]] = []
        self.retries: list[tuple[str, int, int]] = []
        self.git_recoveries: list[tuple[str, str]] = []

    def on_rate_limit(self, agent_name: str, message: str) -> None:
        self.rate_limits.append((agent_name, message))

    def on_rate_limit_wait(self, agent_name: str, wait_seconds: float) -> None:
        self.rate_limit_waits.append((agent_name, wait_seconds))

    def on_retry(self, agent_name: str, attempt: int, max_attempts: int) -> None:
        self.retries.append((agent_name, attempt, max_attempts))

    def on_git_recovery(self, action: str, detail: str) -> None:
        self.git_recoveries.append((action, detail))


def _build_agent_result(
    *,
    agent_name: str,
    output_path: Path,
    exit_code: int,
    output_empty: bool,
    error: str | None = None,
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

    async def fake_auto_commit(self: object) -> bool:
        del self
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

    async def fake_auto_commit(self: object) -> bool:
        del self
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
        retry_policy=RetryPolicy(max_retries=2, failure_mode=FailureMode.FAIL_FAST),
    )

    # Assert
    assert attempts == 2
    assert result.exit_code == 0
    assert setup.ui_callback.git_recoveries == [("auto_commit", "cleaned dirty tree before retry")]


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
        )

    async def fake_is_tree_dirty(self: object) -> bool:
        del self
        return False

    async def fake_auto_commit(self: object) -> bool:
        del self
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
