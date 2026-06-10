"""End-to-end AgentRunner integration matrix across all five formats (WP-27).

Every combination drives the full runner path — real subprocess (the
mock agent CLI in tests/fixtures/mock_agent.py), tee to ``.stream``, log
capture, stream parsing, monitoring, and output extraction — using the same
canonical wire sessions the parser fixtures use.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from orchcore.registry.agent import AgentConfig, AgentMode, OutputExtraction
from orchcore.runner.subprocess import AgentRunner
from orchcore.stream.events import (
    AgentErrorCategory,
    AgentResult,
    StreamEvent,
    StreamEventType,
    StreamFormat,
)
from tests.fixtures.sessions import (
    CANONICAL_SESSIONS,
    DIRECT_FILE_CONTENT,
    EXPECTED_CANONICAL_TEXT,
    STDERR_ERROR_TEXT,
    STDERR_RATE_LIMIT_TEXT,
)

MOCK_AGENT = Path(__file__).resolve().parents[1] / "fixtures" / "mock_agent.py"

EXPECTED_OK_EVENT_TYPES: dict[StreamFormat, list[StreamEventType]] = {
    StreamFormat.CLAUDE: [
        StreamEventType.INIT,
        StreamEventType.TOOL_START,
        StreamEventType.TOOL_EXEC,
        StreamEventType.TOOL_DONE,
        StreamEventType.TEXT,
        StreamEventType.RESULT,
    ],
    StreamFormat.CODEX: [
        StreamEventType.INIT,
        StreamEventType.TOOL_START,
        StreamEventType.TOOL_EXEC,
        StreamEventType.TOOL_DONE,
        StreamEventType.TEXT,
        StreamEventType.RESULT,
    ],
    StreamFormat.COPILOT: [
        StreamEventType.INIT,
        StreamEventType.TOOL_START,
        StreamEventType.TOOL_DONE,
        StreamEventType.TEXT,
    ],
    StreamFormat.OPENCODE: [
        StreamEventType.INIT,
        StreamEventType.TOOL_START,
        StreamEventType.TOOL_DONE,
        StreamEventType.TEXT,
        StreamEventType.RESULT,
    ],
    StreamFormat.GEMINI: [
        StreamEventType.TOOL_START,
        StreamEventType.TEXT,
        StreamEventType.RESULT,
    ],
}


def _mock_agent(
    stream_format: StreamFormat,
    scenario: str,
    strategy: OutputExtraction.Strategy,
) -> AgentConfig:
    return AgentConfig(
        name=f"mock-{stream_format.value}",
        binary=sys.executable,
        subcommand=str(MOCK_AGENT),
        model="mock-model",
        flags={AgentMode.PLAN: ["--format", stream_format.value, "--scenario", scenario]},
        stream_format=stream_format,
        output_extraction=OutputExtraction(strategy=strategy),
        # The advisory version check has dedicated runner tests; keep the
        # matrix to one subprocess per combo.
        version_command=(),
    )


async def _run(agent: AgentConfig, tmp_path: Path) -> tuple[AgentResult, list[StreamEvent]]:
    events: list[StreamEvent] = []
    result = await AgentRunner().run(
        agent,
        "run integration matrix",
        tmp_path / "output.md",
        mode=AgentMode.PLAN,
        on_event=events.append,
    )
    return result, events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "strategy",
    [OutputExtraction.Strategy.JQ_FILTER, OutputExtraction.Strategy.STDOUT_CAPTURE],
)
@pytest.mark.parametrize("stream_format", list(StreamFormat))
async def test_ok_scenario_across_formats_and_strategies(
    stream_format: StreamFormat,
    strategy: OutputExtraction.Strategy,
    tmp_path: Path,
) -> None:
    agent = _mock_agent(stream_format, "ok", strategy)

    result, events = await _run(agent, tmp_path)

    assert result.exit_code == 0
    assert result.error is None
    assert result.error_category is None
    assert result.json_parse_error_count == 0
    assert result.wire_validation_error_count == 0
    assert not result.output_empty

    # Extracted output: both strategies join the stream's TEXT content.
    output = (tmp_path / "output.md").read_text(encoding="utf-8")
    assert output == EXPECTED_CANONICAL_TEXT[stream_format.value]

    # The teed .stream file carries the exact canonical wire session.
    stream_lines = (tmp_path / "output.stream").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in stream_lines] == CANONICAL_SESSIONS[stream_format.value]

    # Nothing was written to stderr on the happy path.
    assert (tmp_path / "output.log").read_text(encoding="utf-8") == ""

    assert [event.event_type for event in events] == EXPECTED_OK_EVENT_TYPES[stream_format]


@pytest.mark.asyncio
async def test_ok_scenario_direct_file_extraction(tmp_path: Path) -> None:
    """Codex-style ``-o`` direct-file output through the real argv path."""
    agent = _mock_agent(StreamFormat.CODEX, "ok", OutputExtraction.Strategy.DIRECT_FILE)

    result, events = await _run(agent, tmp_path)

    assert result.exit_code == 0
    assert result.error is None
    assert not result.output_empty
    assert (tmp_path / "output.md").read_text(encoding="utf-8") == DIRECT_FILE_CONTENT
    assert [event.event_type for event in events] == EXPECTED_OK_EVENT_TYPES[StreamFormat.CODEX]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_format", list(StreamFormat))
async def test_error_scenario_across_formats(
    stream_format: StreamFormat,
    tmp_path: Path,
) -> None:
    """WP-15 regression net through a real subprocess for every format.

    Claude/Codex/OpenCode can express a terminal in-stream error and exit 0:
    the stream-reported failure must reach the result as STREAM_ERROR. The
    Copilot and Gemini wire formats have no terminal error frame, so their
    mock fails like a real CLI (stderr + exit 1) -> NONZERO_EXIT.
    """
    agent = _mock_agent(stream_format, "error", OutputExtraction.Strategy.JQ_FILTER)

    result, events = await _run(agent, tmp_path)

    assert events, "error scenario must still produce stream events"
    assert result.json_parse_error_count == 0
    assert result.wire_validation_error_count == 0
    if stream_format in (StreamFormat.COPILOT, StreamFormat.GEMINI):
        assert result.exit_code == 1
        assert result.error_category is AgentErrorCategory.NONZERO_EXIT
        assert result.error is not None
        assert STDERR_ERROR_TEXT in result.error
    else:
        assert result.exit_code == 0
        assert result.error == "mock structured failure"
        assert result.error_category is AgentErrorCategory.STREAM_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_format", list(StreamFormat))
async def test_rate_limit_scenario_across_formats(
    stream_format: StreamFormat,
    tmp_path: Path,
) -> None:
    """Typed RATE_LIMIT frames (Claude/Codex/Gemini, exit 0) and the stderr
    fallback classifier (Copilot/OpenCode, exit 1) both land as RATE_LIMIT."""
    agent = _mock_agent(stream_format, "rate-limit", OutputExtraction.Strategy.JQ_FILTER)

    result, _events = await _run(agent, tmp_path)

    assert result.error_category is AgentErrorCategory.RATE_LIMIT
    assert result.error is not None
    if stream_format in (StreamFormat.COPILOT, StreamFormat.OPENCODE):
        assert result.exit_code == 1
        assert STDERR_RATE_LIMIT_TEXT in result.error
        # "try again in 60 seconds" is parsed at the source (WP-18).
        assert result.rate_limit_reset_seconds == 60
    else:
        assert result.exit_code == 0
        expected_reset = 60 if stream_format is StreamFormat.GEMINI else 5
        assert result.rate_limit_reset_seconds == expected_reset


@pytest.mark.asyncio
async def test_claude_end_to_end_field_detail(tmp_path: Path) -> None:
    """Deep per-field assertions for one format (successor of the
    mock_claude.sh harness test)."""
    agent = _mock_agent(StreamFormat.CLAUDE, "ok", OutputExtraction.Strategy.JQ_FILTER)
    output_path = tmp_path / "output.md"

    result, events = await _run(agent, tmp_path)

    assert result.agent_name == "mock-claude"
    assert result.output_path == output_path
    assert result.stream_path == output_path.with_suffix(".stream")
    assert result.log_path == output_path.with_suffix(".log")
    assert result.exit_code == 0
    assert result.duration is not None
    assert str(result.cost_usd) == "0.0123"
    assert result.num_turns == 3
    assert result.session_id == "sess-123"
    assert result.agent_version is None  # version check disabled in the matrix

    tool_start = events[1]
    assert tool_start.tool_name == "Read"
    assert tool_start.tool_id == "tb-1"
    assert tool_start.tool_status == "running"

    tool_done = events[3]
    assert tool_done.tool_name == "Read"
    assert tool_done.tool_id == "tb-1"
    assert tool_done.tool_detail == "foo.py"
    assert tool_done.tool_status == "done"

    text_event = events[4]
    assert text_event.text_full == "# Plan\n\nAdd the requested tests."
