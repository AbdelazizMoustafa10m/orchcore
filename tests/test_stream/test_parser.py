from __future__ import annotations

import json
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from orchcore.stream.events import StreamEvent, StreamEventType, StreamFormat
from orchcore.stream.parser import StreamParser

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _flatten_events(parser: StreamParser, lines: list[str]) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    for line in lines:
        events.extend(parser.parse_line(line))
    return events


def test_parse_claude_stream_events(claude_jsonl_lines: list[str]) -> None:
    events = _flatten_events(StreamParser(StreamFormat.CLAUDE), claude_jsonl_lines)

    assert [event.event_type for event in events] == [
        StreamEventType.INIT,
        StreamEventType.TOOL_START,
        StreamEventType.TOOL_EXEC,  # content_block_delta with input_json_delta
        StreamEventType.TOOL_DONE,
        StreamEventType.TEXT,
        StreamEventType.RESULT,
    ]
    assert events[0].session_id == "sess-123"
    assert events[1].tool_name == "Read"
    assert events[1].tool_id == "tb-1"
    assert events[3].tool_detail == "foo.py"
    assert events[4].text_preview == "# Plan\n\nAdd the requested tests."
    assert events[5].cost_usd == Decimal("0.0123")
    assert events[5].num_turns == 3


def test_parse_codex_stream_events(codex_jsonl_lines: list[str]) -> None:
    events = _flatten_events(StreamParser(StreamFormat.CODEX), codex_jsonl_lines)

    assert [event.event_type for event in events] == [
        StreamEventType.INIT,
        StreamEventType.TOOL_START,
        StreamEventType.TOOL_EXEC,  # response.output_item.delta
        StreamEventType.TOOL_DONE,
        StreamEventType.TEXT,
        StreamEventType.RESULT,
    ]
    assert events[1].tool_name == "function_call"
    assert events[3].tool_status == "done"
    assert events[4].text_preview == "Codex summary"


def test_parse_copilot_stream_events(copilot_jsonl_lines: list[str]) -> None:
    events = _flatten_events(StreamParser(StreamFormat.COPILOT), copilot_jsonl_lines)

    assert [event.event_type for event in events] == [
        StreamEventType.INIT,
        StreamEventType.TOOL_START,
        StreamEventType.TOOL_DONE,
        StreamEventType.TEXT,
    ]
    assert events[1].tool_name == "Read"
    assert events[1].tool_detail == "src/planora/core/config.py"
    assert events[2].tool_status == "done"
    assert events[3].text_preview == "Copilot response text"


def test_parse_copilot_preserves_metadata_on_implicit_init() -> None:
    parser = StreamParser(StreamFormat.COPILOT)

    events = parser.parse_line(
        json.dumps(
            {
                "sessionId": "copilot-session-1",
                "toolName": "Read",
                "parameters": {"file_path": "README.md"},
            }
        )
    )

    assert [event.event_type for event in events] == [
        StreamEventType.INIT,
        StreamEventType.TOOL_START,
    ]
    assert events[0].session_id == "copilot-session-1"
    assert events[1].tool_detail == "README.md"


def test_parse_opencode_stream_events(opencode_jsonl_lines: list[str]) -> None:
    events = _flatten_events(StreamParser(StreamFormat.OPENCODE), opencode_jsonl_lines)

    assert [event.event_type for event in events] == [
        StreamEventType.INIT,
        StreamEventType.TOOL_START,
        StreamEventType.TOOL_DONE,
        StreamEventType.TEXT,
        StreamEventType.RESULT,
    ]
    assert events[1].tool_name == "bash"
    assert events[1].tool_detail == "$ ls -la"
    assert events[3].text_preview == "OpenCode response text"


def test_parse_gemini_stream_events(gemini_jsonl_lines: list[str]) -> None:
    events = _flatten_events(StreamParser(StreamFormat.GEMINI), gemini_jsonl_lines)

    assert [event.event_type for event in events] == [
        StreamEventType.TOOL_START,
        StreamEventType.TEXT,
        StreamEventType.RESULT,
    ]
    assert events[0].tool_name == "web_search_exa"
    assert events[0].tool_detail == 'Web "planora tests"'
    assert events[1].text_preview == "Gemini response text"


def test_parse_gemini_unknown_lines_emit_init_then_heartbeat() -> None:
    parser = StreamParser(StreamFormat.GEMINI)
    lines = [json.dumps({"unknown": index}) for index in range(10)]

    events = _flatten_events(parser, lines)

    assert [event.event_type for event in events] == [
        StreamEventType.INIT,
        StreamEventType.HEARTBEAT,
    ]
    assert events[1].text_preview == "Gemini processing (line 10)"


@pytest.mark.parametrize(
    ("line", "expected_type"),
    [
        pytest.param(
            json.dumps(
                {
                    "type": "error",
                    "code": "internal_error",
                    "message": "boom",
                    "exit_code": 2,
                }
            ),
            StreamEventType.ERROR,
            id="codex-error",
        ),
        pytest.param(
            json.dumps({"type": "error", "code": "rate_limit_exceeded", "retry_after_ms": 5000}),
            StreamEventType.RATE_LIMIT,
            id="codex-rate-limit",
        ),
    ],
)
def test_parse_codex_error_events(line: str, expected_type: StreamEventType) -> None:
    events = StreamParser(StreamFormat.CODEX).parse_line(line)

    assert len(events) == 1
    assert events[0].event_type == expected_type


def test_parse_line_returns_empty_for_malformed_json(caplog: pytest.LogCaptureFixture) -> None:
    parser = StreamParser(StreamFormat.CLAUDE)

    with caplog.at_level("WARNING"):
        events = parser.parse_line("{not-json")

    assert events == []
    assert "Malformed JSON line" in caplog.text


def test_parse_line_suppresses_excess_malformed_json_warnings_and_recovers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange
    parser = StreamParser(StreamFormat.CLAUDE)
    valid_line = json.dumps({"type": "system", "subtype": "init", "session_id": "sess-456"})

    # Act
    with caplog.at_level("WARNING"):
        for _ in range(5):
            assert parser.parse_line("{not-json") == []
        recovered_events = parser.parse_line(valid_line)

    # Assert
    assert parser.json_parse_error_count == 5
    assert caplog.text.count("Malformed JSON line") == 3
    assert "Suppressing further malformed JSON warnings" in caplog.text
    assert [event.event_type for event in recovered_events] == [StreamEventType.INIT]
    assert recovered_events[0].session_id == "sess-456"


@pytest.mark.parametrize("line", ["", "   ", "\n", "\t  "])
def test_parse_line_returns_empty_for_blank_lines_without_logging(
    line: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Arrange
    parser = StreamParser(StreamFormat.CODEX)

    # Act
    with caplog.at_level("WARNING"):
        events = parser.parse_line(line)

    # Assert
    assert events == []
    assert caplog.text == ""


@pytest.mark.parametrize(
    ("stream_format", "line", "expected_types"),
    [
        pytest.param(
            StreamFormat.CLAUDE,
            json.dumps({"type": "thread.started"}),
            [],
            id="claude-ignores-codex-event",
        ),
        pytest.param(
            StreamFormat.CODEX,
            json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1"}),
            [],
            id="codex-ignores-claude-event",
        ),
        pytest.param(
            StreamFormat.OPENCODE,
            json.dumps({"toolName": "Read", "parameters": {"file_path": "README.md"}}),
            [],
            id="opencode-ignores-copilot-shape",
        ),
        pytest.param(
            StreamFormat.COPILOT,
            json.dumps({"type": "step_start"}),
            [StreamEventType.INIT],
            id="copilot-emits-implicit-init-for-foreign-shape",
        ),
    ],
)
def test_parse_line_handles_format_mismatches_without_crashing(
    stream_format: StreamFormat,
    line: str,
    expected_types: list[StreamEventType],
) -> None:
    # Arrange
    parser = StreamParser(stream_format)

    # Act
    events = parser.parse_line(line)

    # Assert
    assert [event.event_type for event in events] == expected_types


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "expected"),
    [
        ("Read", {"file_path": "README.md"}, "README.md"),
        ("Grep", {"pattern": "needle" * 20}, ("needle" * 20)[:50]),
        ("Bash", {"command": "pytest tests/test_stream.py"}, "$ pytest tests/test_stream.py"),
        ("Agent", {"description": "review auth flow"}, 'Agent "review auth flow"'),
        ("mcp__exa__web_search_exa", {"query": "planora"}, 'Web "planora"'),
    ],
)
def test_extract_tool_detail_covers_common_tool_shapes(
    tool_name: str,
    tool_input: dict[str, str],
    expected: str,
) -> None:
    assert StreamParser._extract_tool_detail(tool_name, tool_input) == expected


def test_to_decimal_returns_none_for_invalid_values() -> None:
    assert StreamParser._to_decimal("not-a-decimal") is None


@pytest.mark.asyncio
async def test_parse_stream_yields_async_events(copilot_jsonl_lines: list[str]) -> None:
    async def raw_lines() -> AsyncIterator[str]:
        for line in copilot_jsonl_lines:
            yield line

    parser = StreamParser(StreamFormat.COPILOT)
    events = [event async for event in parser.parse_stream(raw_lines())]

    assert [event.event_type for event in events] == [
        StreamEventType.INIT,
        StreamEventType.TOOL_START,
        StreamEventType.TOOL_DONE,
        StreamEventType.TEXT,
    ]
