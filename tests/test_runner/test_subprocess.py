from __future__ import annotations

import logging
import sys
import textwrap
from typing import TYPE_CHECKING

import pytest

from orchcore.registry.agent import AgentConfig, AgentMode, OutputExtraction, ToolSet
from orchcore.runner.subprocess import AgentRunner, _strip_preamble_text, _translate_toolset
from orchcore.stream.events import (
    AgentMonitorSnapshot,
    AgentState,
    StreamEvent,
    StreamEventType,
    StreamFormat,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


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
