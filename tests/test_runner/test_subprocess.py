from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from orchcore.registry.agent import AgentConfig, AgentMode, OutputExtraction, ToolSet
from orchcore.registry.versioning import IncompatibleVersionSpec
from orchcore.runner.subprocess import (
    AgentRunner,
    _detect_agent_version,
    _LineBuffer,
    _log_version_compatibility,
    _resolve_result_state,
    _shutdown_process,
    _strip_preamble_text,
    _translate_toolset,
    build_agent_env,
)
from orchcore.stream.events import (
    AgentErrorCategory,
    AgentMonitorSnapshot,
    AgentState,
    StreamEvent,
    StreamEventType,
    StreamFormat,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _RecordingUICallback:
    def __init__(self) -> None:
        self.events: list[StreamEvent] = []
        self.stalls: list[tuple[str, float]] = []

    def on_agent_event(self, event: StreamEvent) -> None:
        self.events.append(event)

    def on_stall_detected(self, agent_name: str, duration: float) -> None:
        self.stalls.append((agent_name, duration))


def test_build_command_uses_mode_flags(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    command = AgentRunner._build_command(
        sample_agent_config,
        "write tests",
        tmp_path / "output.md",
        AgentMode.PLAN,
    )

    assert command == [
        "echo",
        "-p",
        "write tests",
        "--model",
        "test-model",
        "--verbose",
    ]


def test_empty_subcommand_omitted_from_argv(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    """F2 regression: ``subcommand = ""`` must not become a literal ``''``
    argv element (previously ``['codex', '', 'PROMPT', ...]``)."""
    agent = sample_agent_config.model_copy(update={"subcommand": ""})

    command = AgentRunner._build_command(
        agent,
        "write tests",
        tmp_path / "output.md",
        AgentMode.PLAN,
    )

    assert "" not in command
    assert command == [
        "echo",
        "write tests",
        "--model",
        "test-model",
        "--verbose",
    ]


def test_build_command_stdin_omits_prompt_from_argv(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    agent = sample_agent_config.model_copy(update={"prompt_via": "stdin"})

    command = AgentRunner._build_command(
        agent,
        "write tests",
        tmp_path / "output.md",
        AgentMode.PLAN,
    )

    assert "write tests" not in command
    assert command == [
        "echo",
        "-p",
        "--model",
        "test-model",
        "--verbose",
    ]


def test_build_command_stdin_appends_sentinel_in_place_of_prompt(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    """Codex-style ``codex exec -`` placeholder, kept in registry data."""
    agent = sample_agent_config.model_copy(
        update={"subcommand": "exec", "prompt_via": "stdin", "stdin_sentinel": "-"}
    )

    command = AgentRunner._build_command(
        agent,
        "write tests",
        tmp_path / "output.md",
        AgentMode.PLAN,
    )

    assert "write tests" not in command
    assert command[:3] == ["echo", "exec", "-"]


def test_build_command_appends_direct_file_output_flag(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    agent = sample_agent_config.model_copy(
        update={
            "output_extraction": OutputExtraction(
                strategy=OutputExtraction.Strategy.DIRECT_FILE,
            )
        }
    )

    command = AgentRunner._build_command(
        agent,
        "write tests",
        tmp_path / "output.md",
        AgentMode.FIX,
    )

    assert command[-2:] == ["-o", str(tmp_path / "output.md")]


def test_build_agent_env_filters_sensitive_defaults_and_overlays_env_vars(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "orchcore.runner.subprocess.os.environ",
        {
            "PATH": "/bin",
            "HOME": "/home/test",
            "ANTHROPIC_API_KEY": "ambient-anthropic",
            "OPENAI_API_KEY": "ambient-openai",
            "GITHUB_TOKEN": "ambient-github",
            "CUSTOM": "kept",
        },
    )
    agent = sample_agent_config.model_copy(
        update={"env_vars": {"OPENAI_API_KEY": "explicit-openai", "EXTRA": "1"}}
    )

    env = build_agent_env(agent)

    assert env == {
        "PATH": "/bin",
        "HOME": "/home/test",
        "CUSTOM": "kept",
        "OPENAI_API_KEY": "explicit-openai",
        "EXTRA": "1",
    }


def test_build_agent_env_passlist_readds_filtered_names(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "orchcore.runner.subprocess.os.environ",
        {"PATH": "/bin", "ANTHROPIC_API_KEY": "ambient-anthropic"},
    )
    agent = sample_agent_config.model_copy(update={"env_passlist": ["ANTHROPIC_API_KEY"]})

    env = build_agent_env(agent)

    assert env == {"PATH": "/bin", "ANTHROPIC_API_KEY": "ambient-anthropic"}


def test_build_agent_env_inherit_keeps_everything(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_env = {"PATH": "/bin", "ANTHROPIC_API_KEY": "ambient-anthropic"}
    monkeypatch.setattr("orchcore.runner.subprocess.os.environ", source_env)
    agent = sample_agent_config.model_copy(update={"env_policy": "inherit"})

    assert build_agent_env(agent) == source_env


def test_build_agent_env_clean_uses_exact_case_on_posix(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("orchcore.runner.subprocess.os.name", "posix")
    monkeypatch.setattr(
        "orchcore.runner.subprocess.os.environ",
        {"PATH": "/bin", "path": "/bad", "HOME": "/home/test", "home": "/bad"},
    )
    agent = sample_agent_config.model_copy(update={"env_policy": "clean"})

    assert build_agent_env(agent) == {"PATH": "/bin", "HOME": "/home/test"}


def test_build_agent_env_clean_matches_case_insensitively_on_windows(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("orchcore.runner.subprocess.os.name", "nt")
    monkeypatch.setattr(
        "orchcore.runner.subprocess.os.environ",
        {"Path": r"C:\Windows", "SystemRoot": r"C:\Windows", "lowercase_custom": "drop"},
    )
    agent = sample_agent_config.model_copy(update={"env_policy": "clean"})

    assert build_agent_env(agent) == {"Path": r"C:\Windows", "SystemRoot": r"C:\Windows"}


@pytest.mark.parametrize(
    ("stream_format", "toolset", "expected_flags"),
    [
        pytest.param(
            StreamFormat.CLAUDE,
            ToolSet(internal=["Read"], mcp=["exa"], max_turns=2),
            [
                "--allowedTools",
                "Read,exa",
                "--max-turns",
                "2",
                "--verbose",
                "--output-format",
                "stream-json",
                "--include-partial-messages",
            ],
            id="claude",
        ),
        pytest.param(
            StreamFormat.CODEX,
            ToolSet(permission="workspace-write"),
            ["-s", "workspace-write", "--json"],
            id="codex",
        ),
        pytest.param(
            StreamFormat.GEMINI,
            ToolSet(permission="full-access"),
            ["--yolo"],
            id="gemini",
        ),
        pytest.param(
            StreamFormat.COPILOT,
            ToolSet(internal=["Read", "Write"]),
            ["--allow-tool", "read", "--allow-tool", "write"],
            id="copilot",
        ),
        pytest.param(
            StreamFormat.OPENCODE,
            ToolSet(),
            ["--format", "json"],
            id="opencode",
        ),
    ],
)
def test_build_command_uses_translated_toolset_for_each_agent_format(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
    stream_format: StreamFormat,
    toolset: ToolSet,
    expected_flags: list[str],
) -> None:
    # Arrange
    agent = sample_agent_config.model_copy(update={"stream_format": stream_format})

    # Act
    command = AgentRunner._build_command(
        agent,
        "write tests",
        tmp_path / "output.md",
        AgentMode.PLAN,
        toolset,
    )

    # Assert
    assert command == [
        "echo",
        "-p",
        "write tests",
        "--model",
        "test-model",
        *expected_flags,
    ]


@pytest.mark.parametrize(
    ("stream_format", "toolset", "expected"),
    [
        (
            StreamFormat.CLAUDE,
            ToolSet(internal=["Read"], mcp=["exa"], max_turns=3),
            [
                "--allowedTools",
                "Read,exa",
                "--max-turns",
                "3",
                "--verbose",
                "--output-format",
                "stream-json",
                "--include-partial-messages",
            ],
        ),
        (
            StreamFormat.CODEX,
            ToolSet(permission="full-access"),
            ["-s", "full-access", "--json"],
        ),
        (
            StreamFormat.GEMINI,
            ToolSet(permission="full-access"),
            ["--yolo"],
        ),
        (
            StreamFormat.COPILOT,
            ToolSet(internal=["Read", "Write"]),
            ["--allow-tool", "read", "--allow-tool", "write"],
        ),
        (
            StreamFormat.OPENCODE,
            ToolSet(),
            ["--format", "json"],
        ),
    ],
)
def test_translate_toolset_covers_primary_formats(
    sample_agent_config: AgentConfig,
    stream_format: StreamFormat,
    toolset: ToolSet,
    expected: list[str],
) -> None:
    agent = sample_agent_config.model_copy(update={"stream_format": stream_format})

    assert _translate_toolset(agent, toolset) == expected


def test_translate_toolset_for_claude_emits_allowed_tools_for_empty_toolset(
    sample_agent_config: AgentConfig,
) -> None:
    agent = sample_agent_config.model_copy(update={"stream_format": StreamFormat.CLAUDE})

    assert _translate_toolset(agent, ToolSet()) == [
        "--allowedTools",
        "",
        "--max-turns",
        "25",
        "--verbose",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
    ]


def test_translate_toolset_for_codex_skips_empty_permission(
    sample_agent_config: AgentConfig,
) -> None:
    agent = sample_agent_config.model_copy(update={"stream_format": StreamFormat.CODEX})

    assert _translate_toolset(agent, ToolSet(permission="")) == ["--json"]


def test_translate_toolset_for_codex_logs_ignored_zero_max_turns(
    sample_agent_config: AgentConfig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = sample_agent_config.model_copy(update={"stream_format": StreamFormat.CODEX})

    with caplog.at_level(logging.DEBUG, logger="orchcore.runner.subprocess"):
        flags = _translate_toolset(agent, ToolSet(max_turns=0))

    assert flags == ["-s", "read-only", "--json"]
    assert "codex ignores ToolSet.max_turns=0" in caplog.text


def test_translate_toolset_for_codex_logs_ignored_mcp_tools(
    sample_agent_config: AgentConfig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = sample_agent_config.model_copy(update={"stream_format": StreamFormat.CODEX})

    with caplog.at_level(logging.DEBUG, logger="orchcore.runner.subprocess"):
        flags = _translate_toolset(agent, ToolSet(mcp=["mcp__exa__web_search_exa"]))

    assert flags == ["-s", "read-only", "--json"]
    assert "codex ignores ToolSet.mcp" in caplog.text


@pytest.mark.parametrize(
    ("stream_format", "toolset", "expected_flags"),
    [
        (
            StreamFormat.COPILOT,
            ToolSet(internal=["Read"], permission="full-access", max_turns=7),
            ["--allow-tool", "read"],
        ),
        (
            StreamFormat.OPENCODE,
            ToolSet(permission="workspace-write", max_turns=7),
            ["--format", "json"],
        ),
    ],
)
def test_translate_toolset_logs_unsupported_permission_and_max_turns(
    sample_agent_config: AgentConfig,
    caplog: pytest.LogCaptureFixture,
    stream_format: StreamFormat,
    toolset: ToolSet,
    expected_flags: list[str],
) -> None:
    agent = sample_agent_config.model_copy(update={"stream_format": stream_format})

    with caplog.at_level(logging.DEBUG, logger="orchcore.runner.subprocess"):
        flags = _translate_toolset(agent, toolset)

    assert flags == expected_flags
    assert f"{stream_format.value} ignores ToolSet.permission" in caplog.text
    assert f"{stream_format.value} ignores ToolSet.max_turns=7" in caplog.text


def test_translate_toolset_for_codex_warns_on_unknown_permission(
    sample_agent_config: AgentConfig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = sample_agent_config.model_copy(update={"stream_format": StreamFormat.CODEX})

    with caplog.at_level(logging.WARNING, logger="orchcore.runner.subprocess"):
        flags = _translate_toolset(agent, ToolSet(permission="unknown"))

    assert flags == ["--json"]
    assert "unknown ToolSet.permission='unknown'" in caplog.text


@pytest.mark.parametrize(
    ("stream_format", "expected_flag"),
    [
        (StreamFormat.CODEX, "--json"),
        (StreamFormat.OPENCODE, "--format json"),
    ],
)
@pytest.mark.asyncio
async def test_run_warns_when_mode_flags_lack_required_stream_flags(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    stream_format: StreamFormat,
    expected_flag: str,
) -> None:
    agent = sample_agent_config.model_copy(
        update={
            "stream_format": stream_format,
            "flags": {AgentMode.PLAN: []},
        }
    )

    with caplog.at_level(logging.WARNING, logger="orchcore.runner.subprocess"):
        await AgentRunner().run(
            agent,
            "write tests",
            tmp_path / "output.md",
            mode=AgentMode.PLAN,
            dry_run=True,
        )

    assert expected_flag in caplog.text


@pytest.mark.asyncio
async def test_run_emits_stall_callback_from_bound_ui_handler(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Arrange
    idle_seconds = 12.5

    async def fake_watch(
        self: object,
        events: AsyncIterator[StreamEvent],
    ) -> AsyncIterator[StreamEvent]:
        del self
        emitted_stall = False
        async for event in events:
            yield event
            if not emitted_stall:
                emitted_stall = True
                yield StreamEvent(
                    event_type=StreamEventType.STALL,
                    idle_seconds=idle_seconds,
                )

    script = textwrap.dedent(
        """
        import json

        print(json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1"}), flush=True)
        print(json.dumps({"type": "result", "session_id": "sess-1"}), flush=True)
        """
    ).strip()
    monkeypatch.setattr("orchcore.runner.subprocess.StallDetector.watch", fake_watch)
    agent = sample_agent_config.model_copy(
        update={
            "binary": sys.executable,
            "subcommand": "-c",
            "flags": {AgentMode.PLAN: []},
        }
    )
    callback = _RecordingUICallback()

    # Act
    with pytest.warns(DeprecationWarning, match="Implicit stall-callback discovery"):
        result = await AgentRunner().run(
            agent,
            script,
            tmp_path / "output.md",
            mode=AgentMode.PLAN,
            on_event=callback.on_agent_event,
            stall_check_interval=0.01,
        )

    # Assert
    assert result.exit_code == 0
    stall_events = [event for event in callback.events if event.event_type == StreamEventType.STALL]
    assert len(stall_events) == 1
    assert stall_events[0].idle_seconds == idle_seconds
    assert callback.stalls == [(agent.name, idle_seconds)]


@pytest.mark.asyncio
async def test_run_captures_exit_zero_stream_result_error(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    """F1 regression: a RESULT error on an exit-0 stream reaches AgentResult."""
    script = textwrap.dedent(
        """
        import json

        print(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "partial output"}]},
        }), flush=True)
        print(json.dumps({
            "type": "result",
            "exit_code": 0,
            "error": "structured failure",
        }), flush=True)
        """
    ).strip()
    agent = sample_agent_config.model_copy(
        update={"binary": sys.executable, "subcommand": "-c", "flags": {AgentMode.PLAN: []}}
    )

    result = await AgentRunner().run(
        agent,
        script,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )

    assert result.exit_code == 0
    assert result.error == "structured failure"
    assert result.error_category is AgentErrorCategory.STREAM_ERROR
    assert not result.output_empty


@pytest.mark.asyncio
async def test_run_lets_last_successful_result_clear_prior_rate_limit(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    script = textwrap.dedent(
        """
        import json

        print(json.dumps({"type": "system", "subtype": "rate_limit"}), flush=True)
        print(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "recovered"}]},
        }), flush=True)
        print(json.dumps({"type": "result", "exit_code": 0}), flush=True)
        """
    ).strip()
    agent = sample_agent_config.model_copy(
        update={"binary": sys.executable, "subcommand": "-c", "flags": {AgentMode.PLAN: []}}
    )

    result = await AgentRunner().run(
        agent,
        script,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )

    assert result.exit_code == 0
    assert result.error is None
    assert (tmp_path / "output.md").read_text(encoding="utf-8") == "recovered"


@pytest.mark.asyncio
async def test_run_filters_sensitive_environment_by_default(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """F6-adjacent boundary check: default filtered env strips ambient secrets."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-secret")
    script = textwrap.dedent(
        """
        import json
        import os

        text = os.environ.get("ANTHROPIC_API_KEY", "missing")
        print(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]},
        }), flush=True)
        print(json.dumps({"type": "result", "exit_code": 0}), flush=True)
        """
    ).strip()
    agent = sample_agent_config.model_copy(
        update={"binary": sys.executable, "subcommand": "-c", "flags": {AgentMode.PLAN: []}}
    )

    result = await AgentRunner().run(
        agent,
        script,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )

    assert result.error is None
    assert (tmp_path / "output.md").read_text(encoding="utf-8") == "missing"


@pytest.mark.asyncio
async def test_run_passes_explicit_cwd_to_subprocess(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    """F6 regression: agent subprocess cwd is explicit, not the orchestrator cwd."""
    workdir = tmp_path / "repo"
    workdir.mkdir()
    script = textwrap.dedent(
        """
        import json
        import os

        print(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": os.getcwd()}]},
        }), flush=True)
        print(json.dumps({"type": "result", "exit_code": 0}), flush=True)
        """
    ).strip()
    agent = sample_agent_config.model_copy(
        update={"binary": sys.executable, "subcommand": "-c", "flags": {AgentMode.PLAN: []}}
    )

    result = await AgentRunner().run(
        agent,
        script,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
        cwd=workdir,
    )

    assert result.error is None
    assert (tmp_path / "output.md").read_text(encoding="utf-8") == str(workdir)


@pytest.mark.asyncio
async def test_run_kills_process_on_stall_when_configured(
    sample_agent_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    idle_seconds = 3.25

    async def fake_watch(
        self: object,
        events: AsyncIterator[StreamEvent],
    ) -> AsyncIterator[StreamEvent]:
        del self
        async for event in events:
            yield event
            yield StreamEvent(event_type=StreamEventType.STALL, idle_seconds=idle_seconds)
            return

    script = textwrap.dedent(
        """
        import json
        import time

        print(json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1"}), flush=True)
        time.sleep(10)
        """
    ).strip()
    monkeypatch.setattr("orchcore.runner.subprocess.StallDetector.watch", fake_watch)
    agent = sample_agent_config.model_copy(
        update={
            "binary": sys.executable,
            "subcommand": "-c",
            "flags": {AgentMode.PLAN: []},
            "kill_on_stall": True,
        }
    )
    callback = _RecordingUICallback()

    result = await AgentRunner().run(
        agent,
        script,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
        on_event=callback.on_agent_event,
        on_stall=callback.on_stall_detected,
    )

    assert result.error == f"stalled for {idle_seconds:g}s (kill_on_stall)"
    assert callback.stalls == [(agent.name, idle_seconds)]


@pytest.mark.asyncio
async def test_run_enforces_max_runtime(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    script = "import time; time.sleep(10)"
    agent = sample_agent_config.model_copy(
        update={
            "binary": sys.executable,
            "subcommand": "-c",
            "flags": {AgentMode.PLAN: []},
            "max_runtime": 0.05,
        }
    )

    started = time.monotonic()
    result = await AgentRunner().run(
        agent,
        script,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )

    assert time.monotonic() - started < 2.0
    assert result.error == "max_runtime exceeded after 0.05s"


_LOCK_PROBE_SCRIPT = r"""
import os
import sys

path = sys.argv[1]
with open(path, "r+b") as fh:
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit(1)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit(1)
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
"""


def _can_acquire_lock(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        completed = subprocess.run(  # noqa: S603
            [sys.executable, "-c", _LOCK_PROBE_SCRIPT, str(path)],
            check=False,
            capture_output=True,
            timeout=2.0,
        )
    except subprocess.TimeoutExpired:
        return False
    return completed.returncode == 0


async def _wait_for_path(path: Path, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


async def _wait_for_lock_release(path: Path, *, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _can_acquire_lock(path):
            return True
        await asyncio.sleep(0.02)
    return False


def _terminate_pid(pid: int) -> None:
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGTERM)


@pytest.mark.asyncio
async def test_shutdown_process_terminates_spawned_grandchild(tmp_path: Path) -> None:
    grandchild_path = tmp_path / "grandchild.py"
    child_path = tmp_path / "child.py"
    parent_path = tmp_path / "parent.py"
    lock_path = tmp_path / "grandchild.lock"
    ready_path = tmp_path / "grandchild.ready"
    grandchild_pid_path = tmp_path / "grandchild.pid"

    grandchild_path.write_text(
        textwrap.dedent(
            """
            import os
            import sys
            import time
            from pathlib import Path

            lock_path = Path(sys.argv[1])
            ready_path = Path(sys.argv[2])
            pid_path = Path(sys.argv[3])
            with lock_path.open("w+b") as fh:
                fh.write(b"0")
                fh.flush()
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                pid_path.write_text(str(os.getpid()), encoding="utf-8")
                ready_path.write_text("ready", encoding="utf-8")
                while True:
                    time.sleep(1)
            """
        ).strip(),
        encoding="utf-8",
    )
    child_path.write_text(
        textwrap.dedent(
            """
            import subprocess
            import sys
            import time

            subprocess.Popen([sys.executable, *sys.argv[1:]])  # noqa: S603
            while True:
                time.sleep(1)
            """
        ).strip(),
        encoding="utf-8",
    )
    parent_path.write_text(
        textwrap.dedent(
            """
            import subprocess
            import sys
            import time

            subprocess.Popen([sys.executable, *sys.argv[1:]])  # noqa: S603
            while True:
                time.sleep(1)
            """
        ).strip(),
        encoding="utf-8",
    )

    if os.name != "nt":
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(parent_path),
            str(child_path),
            str(grandchild_path),
            str(lock_path),
            str(ready_path),
            str(grandchild_pid_path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(parent_path),
            str(child_path),
            str(grandchild_path),
            str(lock_path),
            str(ready_path),
            str(grandchild_pid_path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    grandchild_pid: int | None = None
    try:
        await _wait_for_path(ready_path)
        grandchild_pid = int(grandchild_pid_path.read_text(encoding="utf-8"))

        await _shutdown_process(proc)

        lock_released = await _wait_for_lock_release(lock_path)
        if not lock_released and grandchild_pid is not None:
            _terminate_pid(grandchild_pid)
        assert lock_released
    finally:
        if proc.returncode is None:
            await _shutdown_process(proc)


@pytest.mark.asyncio
async def test_shutdown_process_falls_back_when_taskkill_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FakeProcess:
        pid = 12345
        returncode: int | None = None
        kill_calls = 0

        async def wait(self) -> int:
            if self.kill_calls:
                self.returncode = -9
                return -9
            await asyncio.Event().wait()
            return 0

        def kill(self) -> None:
            self.kill_calls += 1

    fake_proc = FakeProcess()

    def fake_taskkill(pid: int) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=["taskkill", str(pid)],
            returncode=1,
            stderr=b"taskkill unavailable",
        )

    async def fake_wait_for(awaitable: object, timeout: float) -> object:
        del timeout
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError

    monkeypatch.setattr("orchcore.runner.subprocess.os.name", "nt")
    monkeypatch.setattr("orchcore.runner.subprocess._kill_tree_windows", fake_taskkill)
    monkeypatch.setattr("orchcore.runner.subprocess.asyncio.wait_for", fake_wait_for)

    with caplog.at_level(logging.WARNING, logger="orchcore.runner.subprocess"):
        await _shutdown_process(fake_proc)  # type: ignore[arg-type]

    assert fake_proc.kill_calls == 1
    assert "taskkill failed for pid 12345" in caplog.text


@pytest.mark.asyncio
async def test_run_uses_stdout_fallback_for_error_when_stderr_is_empty(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    # Arrange
    script = textwrap.dedent(
        """
        print("stdout failure", flush=True)
        raise SystemExit(7)
        """
    ).strip()
    agent = sample_agent_config.model_copy(
        update={
            "binary": sys.executable,
            "subcommand": "-c",
            "flags": {AgentMode.PLAN: []},
        }
    )

    # Act
    result = await AgentRunner().run(
        agent,
        script,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )

    # Assert
    assert result.exit_code == 7
    assert result.error == "stdout failure"


@pytest.mark.asyncio
async def test_run_writes_full_text_not_truncated_preview(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    # Arrange — write a helper script that emits a TEXT event with text > 200 chars
    long_text = "X" * 250
    script_path = tmp_path / "emit_long_text.py"
    script_path.write_text(
        textwrap.dedent(
            """\
            import json
            long_text = "X" * 250
            print(json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
                flush=True)
            print(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": long_text}]}
            }), flush=True)
            print(json.dumps({"type": "result", "exit_code": 0}), flush=True)
            """
        ),
        encoding="utf-8",
    )
    agent = sample_agent_config.model_copy(
        update={
            "binary": sys.executable,
            "subcommand": str(script_path),
            "flags": {AgentMode.PLAN: []},
        }
    )

    # Act
    result = await AgentRunner().run(
        agent,
        "",
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )

    # Assert — persisted output contains the full 250-char text, not just the 200-char preview
    assert result.exit_code == 0
    output = (tmp_path / "output.md").read_text(encoding="utf-8")
    assert long_text in output
    assert len(output) >= 250


@pytest.mark.asyncio
async def test_run_emits_failed_snapshot_when_process_exits_nonzero(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    # Arrange — script exits non-zero without emitting a RESULT stream event
    script = textwrap.dedent(
        """
        raise SystemExit(3)
        """
    ).strip()
    agent = sample_agent_config.model_copy(
        update={
            "binary": sys.executable,
            "subcommand": "-c",
            "flags": {AgentMode.PLAN: []},
        }
    )
    snapshots: list[AgentMonitorSnapshot] = []

    # Act
    result = await AgentRunner().run(
        agent,
        script,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
        on_snapshot=snapshots.append,
    )

    # Assert — the final on_snapshot call reflects the failure state
    assert result.exit_code == 3
    assert len(snapshots) >= 1
    final_snapshot = snapshots[-1]
    assert final_snapshot.state == AgentState.FAILED


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("preamble\n# Title\nbody", "# Title\nbody"),
        ("# Title\nbody", "# Title\nbody"),
        ("no heading here", "no heading here"),
    ],
)
def test_strip_preamble_text(text: str, expected: str) -> None:
    assert _strip_preamble_text(text) == expected


@pytest.mark.parametrize(
    ("state_kwargs", "expected_category", "expected_reset"),
    [
        pytest.param(
            {"timed_out": True, "max_runtime": 5.0},
            AgentErrorCategory.TIMEOUT,
            None,
            id="max-runtime-timeout",
        ),
        pytest.param(
            {"stalled_out": True, "stall_idle_seconds": 30.0},
            AgentErrorCategory.STALL_TIMEOUT,
            None,
            id="kill-on-stall",
        ),
        pytest.param(
            {
                "exit_code": 0,
                "stream_error": "rate limited, try again in 17 seconds",
                "stream_error_category": AgentErrorCategory.RATE_LIMIT,
            },
            AgentErrorCategory.RATE_LIMIT,
            17,
            id="typed-rate-limit-event-exit-zero",
        ),
        pytest.param(
            {
                "exit_code": 0,
                "stream_error": "rate limited, try again in 90 seconds",
                "stream_error_category": AgentErrorCategory.RATE_LIMIT,
                "stream_rate_limit_retry_delay_ms": 5000,
            },
            AgentErrorCategory.RATE_LIMIT,
            5,
            id="typed-rate-limit-delay-prefers-structured-value",
        ),
        pytest.param(
            {
                "exit_code": 0,
                "stream_error": "structured failure",
                "stream_error_category": AgentErrorCategory.STREAM_ERROR,
            },
            AgentErrorCategory.STREAM_ERROR,
            None,
            id="stream-error-exit-zero",
        ),
        pytest.param(
            {"exit_code": 2, "stderr_chunks": ["disk exploded\n"]},
            AgentErrorCategory.NONZERO_EXIT,
            None,
            id="nonzero-exit-without-stream-category",
        ),
        pytest.param(
            {"exit_code": 1, "stderr_chunks": ["rate limit exceeded\n"]},
            AgentErrorCategory.RATE_LIMIT,
            None,
            id="nonzero-exit-fallback-detector-upgrade",
        ),
        pytest.param(
            {
                "exit_code": 1,
                "stderr_chunks": ["process blew up\n"],
                "stream_error": "rate limited, try again in 90 seconds",
                "stream_error_category": AgentErrorCategory.RATE_LIMIT,
            },
            AgentErrorCategory.RATE_LIMIT,
            90,
            id="nonzero-exit-typed-stream-rate-limit-wins",
        ),
        pytest.param(
            {"exit_code": 0, "snap_state": AgentState.RATE_LIMITED},
            AgentErrorCategory.RATE_LIMIT,
            None,
            id="terminal-rate-limited-state-without-message",
        ),
        pytest.param(
            {"exit_code": 0, "snap_state": AgentState.FAILED},
            AgentErrorCategory.STREAM_ERROR,
            None,
            id="terminal-failed-state-without-message",
        ),
        pytest.param(
            {"exit_code": 0},
            None,
            None,
            id="clean-success",
        ),
    ],
)
def test_resolve_result_state_category_matrix(
    state_kwargs: dict[str, object],
    expected_category: AgentErrorCategory | None,
    expected_reset: int | None,
) -> None:
    """WP-18 population map: one case per row of the category table."""
    defaults: dict[str, object] = {
        "exit_code": 0,
        "stderr_chunks": [],
        "stdout_chunks": [],
        "stream_error": None,
        "stream_error_category": None,
        "snap_state": AgentState.COMPLETED,
        "timed_out": False,
        "max_runtime": None,
        "stalled_out": False,
        "stall_idle_seconds": None,
    }
    defaults.update(state_kwargs)

    error, category, reset_seconds = _resolve_result_state(**defaults)  # type: ignore[arg-type]

    assert category is expected_category
    assert reset_seconds == expected_reset
    if expected_category is None:
        assert error is None
    else:
        assert error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stream_format", "event", "expected_reset_seconds"),
    [
        pytest.param(
            StreamFormat.CLAUDE,
            {"type": "system", "subtype": "rate_limit", "retry_after_ms": 5000},
            5,
            id="claude",
        ),
        pytest.param(
            StreamFormat.CODEX,
            {"type": "error", "code": "rate_limit_exceeded", "retry_after_ms": 5000},
            5,
            id="codex",
        ),
        pytest.param(
            StreamFormat.GEMINI,
            {
                "error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"},
                "retry_after_ms": 60000,
            },
            60,
            id="gemini",
        ),
    ],
)
async def test_run_populates_rate_limit_reset_from_typed_stream_event(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
    stream_format: StreamFormat,
    event: dict[str, object],
    expected_reset_seconds: int,
) -> None:
    script = f"print({json.dumps(event)!r}, flush=True)"
    agent = sample_agent_config.model_copy(
        update={
            "binary": sys.executable,
            "subcommand": "-c",
            "flags": {AgentMode.PLAN: []},
            "stream_format": stream_format,
        }
    )

    result = await AgentRunner().run(
        agent,
        script,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )

    assert result.exit_code == 0
    assert result.error_category is AgentErrorCategory.RATE_LIMIT
    assert result.error is not None
    assert result.rate_limit_reset_seconds == expected_reset_seconds
    assert result.json_parse_error_count == 0


@pytest.mark.asyncio
async def test_run_upgrades_nonzero_exit_to_rate_limit_and_parses_reset(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    """WP-18 fallback classifier: CLIs that never emit typed events still
    produce RATE_LIMIT results when the stderr tail matches, with the reset
    parsed once at the source."""
    script = textwrap.dedent(
        """
        import sys

        print("429 rate limit exceeded, try again in 17 seconds", file=sys.stderr)
        sys.exit(1)
        """
    ).strip()
    agent = sample_agent_config.model_copy(
        update={"binary": sys.executable, "subcommand": "-c", "flags": {AgentMode.PLAN: []}}
    )

    result = await AgentRunner().run(
        agent,
        script,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )

    assert result.exit_code == 1
    assert result.error_category is AgentErrorCategory.RATE_LIMIT
    assert result.rate_limit_reset_seconds == 17
    assert result.error is not None
    assert "429 rate limit exceeded" in result.error


@pytest.mark.asyncio
async def test_run_counts_malformed_json_lines_on_result(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    """WP-18: the parser's malformed-line count reaches AgentResult."""
    script = textwrap.dedent(
        """
        import json

        print(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "useful output"}]},
        }), flush=True)
        print("{not valid json", flush=True)
        print(json.dumps({"type": "result", "exit_code": 0}), flush=True)
        """
    ).strip()
    agent = sample_agent_config.model_copy(
        update={"binary": sys.executable, "subcommand": "-c", "flags": {AgentMode.PLAN: []}}
    )

    result = await AgentRunner().run(
        agent,
        script,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )

    assert result.exit_code == 0
    assert result.error is None
    assert result.json_parse_error_count == 1


@pytest.mark.asyncio
async def test_run_flags_empty_output_category_on_clean_exit(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    agent = sample_agent_config.model_copy(
        update={"binary": sys.executable, "subcommand": "-c", "flags": {AgentMode.PLAN: []}}
    )

    result = await AgentRunner().run(
        agent,
        "pass",
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )

    assert result.exit_code == 0
    assert result.error == "Agent 'test-agent' completed without producing output"
    assert result.output_empty is True
    assert result.error_category is AgentErrorCategory.EMPTY_OUTPUT


def _write_stdin_echo_script(tmp_path: Path) -> tuple[Path, Path]:
    """A fake agent CLI that reads its prompt from stdin and records it."""
    received_path = tmp_path / "received.txt"
    script_path = tmp_path / "fake_agent.py"
    script_path.write_text(
        textwrap.dedent(
            f"""
            import json
            import sys

            data = sys.stdin.read()
            with open({str(received_path)!r}, "w", encoding="utf-8") as fh:
                fh.write(data)
            print(json.dumps({{
                "type": "assistant",
                "message": {{"content": [{{"type": "text", "text": "received"}}]}},
            }}), flush=True)
            print(json.dumps({{"type": "result", "exit_code": 0}}), flush=True)
            """
        ).strip(),
        encoding="utf-8",
    )
    return script_path, received_path


def _stdin_agent(sample_agent_config: AgentConfig, script_path: Path) -> AgentConfig:
    return sample_agent_config.model_copy(
        update={
            "binary": sys.executable,
            "subcommand": str(script_path),
            "flags": {AgentMode.PLAN: []},
            "prompt_via": "stdin",
        }
    )


@pytest.mark.asyncio
async def test_run_delivers_prompt_via_stdin_not_argv(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    """F2/WP-23: under prompt_via="stdin" the child receives the prompt on
    stdin and the prompt never appears in argv."""
    script_path, received_path = _write_stdin_echo_script(tmp_path)
    agent = _stdin_agent(sample_agent_config, script_path)
    prompt = "secret prompt that must stay out of argv"

    command = AgentRunner._build_command(agent, prompt, tmp_path / "output.md", AgentMode.PLAN)
    result = await AgentRunner().run(
        agent,
        prompt,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )

    assert prompt not in command
    assert result.exit_code == 0
    assert result.error is None
    assert received_path.read_text(encoding="utf-8") == prompt
    assert (tmp_path / "output.md").read_text(encoding="utf-8") == "received"


@pytest.mark.asyncio
async def test_run_stdin_large_prompt_completes_without_deadlock(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    """WP-23: a prompt far beyond the OS pipe buffer is fed concurrently with
    stream consumption, so neither side deadlocks."""
    script_path, received_path = _write_stdin_echo_script(tmp_path)
    agent = _stdin_agent(sample_agent_config, script_path)
    prompt = "x" * (256 * 1024)

    result = await AgentRunner().run(
        agent,
        prompt,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )

    assert result.exit_code == 0
    assert result.error is None
    assert received_path.read_text(encoding="utf-8") == prompt


@pytest.mark.asyncio
async def test_run_stdin_dead_child_consumes_pipe_error(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    """WP-23 regression: a child that exits without reading stdin while the
    prompt exceeds the pipe buffer must not leave an unretrieved feeder
    exception; the result reflects the CLI's own exit."""
    script_path = tmp_path / "dead_agent.py"
    script_path.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    agent = _stdin_agent(sample_agent_config, script_path)
    prompt = "x" * (1024 * 1024)

    loop = asyncio.get_running_loop()
    unraisable: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _loop, context: unraisable.append(dict(context)))
    try:
        result = await AgentRunner().run(
            agent,
            prompt,
            tmp_path / "output.md",
            mode=AgentMode.PLAN,
        )
        import gc

        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(None)

    assert result.exit_code == 0
    assert unraisable == []


# ---- WP-26: agent CLI version-compatibility framework ----


@pytest.fixture
def fresh_version_cache(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    """Isolate the module-level version cache for each test."""
    cache: dict[str, str | None] = {}
    monkeypatch.setattr("orchcore.runner.subprocess._VERSION_CACHE", cache)
    return cache


def _version_agent(**overrides: object) -> AgentConfig:
    base: dict[str, object] = {
        "name": "version-agent",
        "binary": sys.executable,
        "model": "test-model",
        "subcommand": "-c",
        "flags": {AgentMode.PLAN: []},
        "stream_format": StreamFormat.CLAUDE,
        "output_extraction": OutputExtraction(
            strategy=OutputExtraction.Strategy.STDOUT_CAPTURE,
        ),
    }
    base.update(overrides)
    return AgentConfig.model_validate(base)


@pytest.mark.asyncio
@pytest.mark.usefixtures("fresh_version_cache")
async def test_detect_agent_version_runs_one_exec_per_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _version_agent(version_command=("-c", "print('tool 4.5.6 ready')"))
    exec_calls = 0
    real_exec = asyncio.create_subprocess_exec

    async def counting_exec(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        nonlocal exec_calls
        exec_calls += 1
        return await real_exec(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("orchcore.runner.subprocess.asyncio.create_subprocess_exec", counting_exec)

    first = await _detect_agent_version(agent, None)
    second = await _detect_agent_version(agent, None)

    assert first == "4.5.6"
    assert second == "4.5.6"
    assert exec_calls == 1  # second call served from the cache


@pytest.mark.asyncio
@pytest.mark.usefixtures("fresh_version_cache")
async def test_detect_agent_version_logs_current_agent_compatibility_from_cache(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    first_agent = _version_agent(
        name="agent-a",
        version_command=("-c", "print('tool 1.9.0 ready')"),
    )
    second_agent = _version_agent(
        name="agent-b",
        version_command=("-c", "raise SystemExit('should not run')"),
        incompatible_versions=(
            IncompatibleVersionSpec(spec="<=2.0.0", reason="stream-json v1 format"),
        ),
    )
    exec_calls = 0
    real_exec = asyncio.create_subprocess_exec

    async def counting_exec(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        nonlocal exec_calls
        exec_calls += 1
        return await real_exec(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("orchcore.runner.subprocess.asyncio.create_subprocess_exec", counting_exec)

    with caplog.at_level(logging.WARNING, logger="orchcore.runner.subprocess"):
        first = await _detect_agent_version(first_agent, None)
        second = await _detect_agent_version(second_agent, None)

    assert first == "1.9.0"
    assert second == "1.9.0"
    assert exec_calls == 1
    assert "Agent agent-b CLI version 1.9.0 is known-incompatible" in caplog.text
    assert "stream-json v1 format" in caplog.text


@pytest.mark.asyncio
@pytest.mark.usefixtures("fresh_version_cache")
async def test_run_populates_agent_version_on_result(
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    script = 'import json; print(json.dumps({"type": "result", "exit_code": 0}), flush=True)'
    agent = sample_agent_config.model_copy(
        update={"binary": sys.executable, "subcommand": "-c", "flags": {AgentMode.PLAN: []}}
    )

    result = await AgentRunner().run(
        agent,
        script,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )

    # Default version_command ("--version",) against the Python interpreter.
    assert result.agent_version is not None
    assert result.agent_version.count(".") >= 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("fresh_version_cache")
async def test_run_skips_version_check_when_disabled_or_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    async def fail_if_called(_agent: AgentConfig, _cwd: Path | None) -> str | None:
        raise AssertionError("version check must not run")

    monkeypatch.setattr("orchcore.runner.subprocess._detect_agent_version", fail_if_called)

    disabled = sample_agent_config.model_copy(
        update={
            "binary": sys.executable,
            "subcommand": "-c",
            "flags": {AgentMode.PLAN: []},
            "version_command": (),
        }
    )
    result = await AgentRunner().run(
        disabled,
        'import json; print(json.dumps({"type": "result", "exit_code": 0}), flush=True)',
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )
    assert result.agent_version is None

    dry = sample_agent_config.model_copy(update={"binary": sys.executable, "subcommand": "-c"})
    dry_result = await AgentRunner().run(
        dry,
        "ignored",
        tmp_path / "dry.md",
        mode=AgentMode.PLAN,
        dry_run=True,
    )
    assert dry_result.agent_version is None


@pytest.mark.asyncio
async def test_version_check_malformed_output_caches_none_and_logs_info_once(
    fresh_version_cache: dict[str, str | None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = _version_agent(version_command=("-c", "print('no version digits here')"))

    with caplog.at_level(logging.INFO, logger="orchcore.runner.subprocess"):
        first = await _detect_agent_version(agent, None)
        second = await _detect_agent_version(agent, None)

    assert first is None
    assert second is None
    assert fresh_version_cache == {sys.executable: None}
    assert caplog.text.count("no version detected") == 1  # cached -> logged once


@pytest.mark.asyncio
async def test_version_check_missing_binary_logs_debug_and_caches_none(
    fresh_version_cache: dict[str, str | None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = _version_agent(binary="definitely-not-a-real-binary-xyz")

    with caplog.at_level(logging.DEBUG, logger="orchcore.runner.subprocess"):
        version = await _detect_agent_version(agent, None)

    assert version is None
    assert fresh_version_cache == {"definitely-not-a-real-binary-xyz": None}
    assert "version command failed" in caplog.text


@pytest.mark.asyncio
@pytest.mark.usefixtures("fresh_version_cache")
async def test_version_subprocess_crosses_explicit_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The version exec must inherit the filtered env and explicit cwd (WP-10/WP-17)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-secret")
    workdir = tmp_path / "repo"
    workdir.mkdir()
    report_path = tmp_path / "boundary.json"
    script = (
        "import json, os, pathlib; "
        f"pathlib.Path({str(report_path)!r}).write_text(json.dumps("
        "{'has_secret': 'ANTHROPIC_API_KEY' in os.environ, 'cwd': os.getcwd()})); "
        "print('9.9.9')"
    )
    agent = _version_agent(version_command=("-c", script))

    version = await _detect_agent_version(agent, workdir)

    assert version == "9.9.9"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["has_secret"] is False  # default env_policy="filtered" applies
    assert Path(report["cwd"]).resolve() == workdir.resolve()


def test_log_version_compatibility_calibrated_levels(
    caplog: pytest.LogCaptureFixture,
) -> None:
    incompatible_agent = _version_agent(
        incompatible_versions=(
            IncompatibleVersionSpec(spec="<=2.0.0", reason="stream-json v1 format"),
        ),
        compatible_versions=(">=2.1,<3",),
    )

    with caplog.at_level(logging.DEBUG, logger="orchcore.runner.subprocess"):
        _log_version_compatibility(incompatible_agent, "1.9.0")
    assert caplog.records[-1].levelno == logging.WARNING
    assert "stream-json v1 format" in caplog.records[-1].getMessage()

    with caplog.at_level(logging.DEBUG, logger="orchcore.runner.subprocess"):
        _log_version_compatibility(incompatible_agent, "2.5.0")
    assert caplog.records[-1].levelno == logging.DEBUG
    assert "within the declared compatible ranges" in caplog.records[-1].getMessage()

    with caplog.at_level(logging.DEBUG, logger="orchcore.runner.subprocess"):
        _log_version_compatibility(incompatible_agent, "3.5.0")
    assert caplog.records[-1].levelno == logging.INFO
    assert "outside the declared compatible ranges" in caplog.records[-1].getMessage()

    incompatible_only_agent = _version_agent(
        incompatible_versions=(
            IncompatibleVersionSpec(spec="<=2.0.0", reason="stream-json v1 format"),
        ),
    )
    with caplog.at_level(logging.DEBUG, logger="orchcore.runner.subprocess"):
        _log_version_compatibility(incompatible_only_agent, "2.5.0")
    assert caplog.records[-1].levelno == logging.INFO
    assert "outside the declared compatible ranges" in caplog.records[-1].getMessage()

    undeclared_agent = _version_agent()
    with caplog.at_level(logging.DEBUG, logger="orchcore.runner.subprocess"):
        _log_version_compatibility(undeclared_agent, "1.0.0")
    assert caplog.records[-1].levelno == logging.DEBUG
    assert "no version expectations declared" in caplog.records[-1].getMessage()


# ---- WP-30: bounded in-memory stream collection ----


def test_line_buffer_below_threshold_behaves_like_plain_list() -> None:
    buffer = _LineBuffer(spill_threshold=1024)

    buffer.append("hello ")
    buffer.append("world\n")

    assert buffer.spilled is False
    assert buffer.getvalue() == "hello world\n"
    assert buffer.tail_lines() == ["hello ", "world\n"]
    buffer.close()


def test_line_buffer_spills_past_threshold_and_bounds_memory() -> None:
    buffer = _LineBuffer(spill_threshold=100)
    lines = [f"line-{index:04d}\n" for index in range(50)]

    for line in lines:
        buffer.append(line)

    assert buffer.spilled is True
    # Once spilled, nothing accumulates in memory anymore.
    assert buffer.buffered_chunk_count == 0
    # Full content still reproduced exactly at write-out time.
    assert buffer.getvalue() == "".join(lines)
    # The tail keeps only the most recent non-blank lines for error derivation.
    assert buffer.tail_lines() == lines[-10:]
    buffer.close()


def test_line_buffer_tail_skips_blank_chunks() -> None:
    buffer = _LineBuffer(spill_threshold=1024)

    buffer.append("real error\n")
    buffer.append("   \n")
    buffer.append("\n")

    assert buffer.tail_lines() == ["real error\n"]
    buffer.close()


def test_line_buffer_close_releases_spill_file() -> None:
    buffer = _LineBuffer(spill_threshold=1)
    buffer.append("spills immediately\n")
    assert buffer.spilled is True

    buffer.close()

    assert buffer.spilled is False
    assert buffer.getvalue() == ""


@pytest.mark.asyncio
async def test_run_chatty_agent_completes_with_spilled_buffers(
    monkeypatch: pytest.MonkeyPatch,
    sample_agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    """A stream larger than the spill threshold still extracts exact output,
    while peak in-memory collection stays bounded (the collector spills)."""
    monkeypatch.setattr("orchcore.runner.subprocess._SPILL_THRESHOLD_CHARS", 1024)

    class _RecordingBuffer(_LineBuffer):
        def __init__(self) -> None:
            super().__init__()
            self.did_spill = False
            created.append(self)

        def append(self, chunk: str) -> None:
            super().append(chunk)
            if self.spilled:
                self.did_spill = True

    created: list[_RecordingBuffer] = []

    monkeypatch.setattr("orchcore.runner.subprocess._LineBuffer", _RecordingBuffer)
    # 60 TEXT events x ~40 chars >> the 1 KiB threshold.
    script = textwrap.dedent(
        """
        import json

        for index in range(60):
            print(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": f"chunk-{index:04d}-padding\\n"}]},
            }), flush=True)
        print(json.dumps({"type": "result", "exit_code": 0}), flush=True)
        """
    ).strip()
    agent = sample_agent_config.model_copy(
        update={
            "binary": sys.executable,
            "subcommand": "-c",
            "flags": {AgentMode.PLAN: []},
            "version_command": (),
        }
    )

    result = await AgentRunner().run(
        agent,
        script,
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
    )

    assert result.exit_code == 0
    assert result.error is None
    expected = "".join(f"chunk-{index:04d}-padding\n" for index in range(60))
    assert (tmp_path / "output.md").read_text(encoding="utf-8") == expected
    # Both the text collector and the teed stdout collector crossed the
    # threshold and spilled; in-memory accumulation is bounded. (The buffers
    # are close()d by run()'s finally, so record spilling as it happens.)
    assert len(created) == 3
    assert sum(1 for buffer in created if buffer.did_spill) >= 2
