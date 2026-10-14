from __future__ import annotations

import json
import logging
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


@pytest.mark.parametrize(
    ("stream_format", "fixture_name", "expected_types", "spot_checks"),
    [
        pytest.param(
            StreamFormat.CLAUDE,
            "claude_jsonl_lines",
            [
                StreamEventType.INIT,
                StreamEventType.TOOL_START,
                StreamEventType.TOOL_EXEC,
                StreamEventType.TOOL_DONE,
                StreamEventType.TEXT,
                StreamEventType.RESULT,
            ],
            {
                0: {"session_id": "sess-123"},
                1: {"tool_name": "Read", "tool_id": "tb-1"},
                3: {"tool_detail": "foo.py"},
                4: {"text_preview": "# Plan\n\nAdd the requested tests."},
                5: {"cost_usd": Decimal("0.0123"), "num_turns": 3},
            },
            id="claude",
        ),
        pytest.param(
            StreamFormat.CODEX,
            "codex_jsonl_lines",
            [
                StreamEventType.INIT,
                StreamEventType.TOOL_START,
                StreamEventType.TOOL_EXEC,
                StreamEventType.TOOL_DONE,
                StreamEventType.TEXT,
                StreamEventType.RESULT,
            ],
            {
                1: {"tool_name": "function_call"},
                3: {"tool_status": "done"},
                4: {"text_preview": "Codex summary"},
            },
            id="codex",
        ),
        pytest.param(
            StreamFormat.COPILOT,
            "copilot_jsonl_lines",
            [
                StreamEventType.INIT,
                StreamEventType.TOOL_START,
                StreamEventType.TOOL_DONE,
                StreamEventType.TEXT,
            ],
            {
                1: {"tool_name": "Read", "tool_detail": "src/planora/core/config.py"},
                2: {"tool_status": "done"},
                3: {"text_preview": "Copilot response text"},
            },
            id="copilot",
        ),
        pytest.param(
            StreamFormat.OPENCODE,
            "opencode_jsonl_lines",
            [
                StreamEventType.INIT,
                StreamEventType.TOOL_START,
                StreamEventType.TOOL_DONE,
                StreamEventType.TEXT,
                StreamEventType.RESULT,
            ],
            {
                1: {"tool_name": "bash", "tool_detail": "$ ls -la"},
                3: {"text_preview": "OpenCode response text"},
            },
            id="opencode",
        ),
        pytest.param(
            StreamFormat.ANTIGRAVITY,
            "antigravity_jsonl_lines",
            [
                StreamEventType.INIT,
                StreamEventType.TOOL_START,
                StreamEventType.TOOL_DONE,
                StreamEventType.TEXT,
                StreamEventType.TEXT,
                StreamEventType.RESULT,
            ],
            {
                0: {"session_id": "conv-1"},
                1: {"tool_name": "view_file", "tool_detail": "/repo/README.md"},
                2: {"tool_id": "conv-1:1", "tool_duration_ms": 120},
                3: {"text_preview": "Antigravity response"},
                5: {"duration_ms": 5770, "num_turns": 1, "session_id": "conv-1"},
            },
            id="antigravity",
        ),
    ],
)
def test_parse_stream_events_by_format(
    request: pytest.FixtureRequest,
    stream_format: StreamFormat,
    fixture_name: str,
    expected_types: list[StreamEventType],
    spot_checks: dict[int, dict[str, object]],
) -> None:
    lines: list[str] = request.getfixturevalue(fixture_name)
    events = _flatten_events(StreamParser(stream_format), lines)

    assert [event.event_type for event in events] == expected_types
    for idx, attrs in spot_checks.items():
        for attr, value in attrs.items():
            assert getattr(events[idx], attr) == value


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


def test_parse_antigravity_ignores_unknown_events_and_steps() -> None:
    parser = StreamParser(StreamFormat.ANTIGRAVITY)
    lines = [
        json.dumps({"unknown": 1}),
        json.dumps({"event": "future_event"}),
        json.dumps({"event": "step_update", "step_update": {"step_type": "future_step"}}),
        json.dumps({"event": "step_update", "step_update": {"step_type": "checkpoint"}}),
        json.dumps({"event": "step_update", "step_update": ["not", "an", "object"]}),
    ]

    assert _flatten_events(parser, lines) == []
    assert parser.wire_validation_error_count == 0


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


def test_claude_text_event_preserves_full_text_beyond_preview_limit() -> None:
    # Arrange — build a Claude assistant TEXT event with text longer than 200 chars
    long_text = "A" * 250
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": long_text},
                ]
            },
        }
    )
    parser = StreamParser(StreamFormat.CLAUDE)

    # Act
    events = parser.parse_line(line)

    # Assert
    assert len(events) == 1
    event = events[0]
    assert event.event_type == StreamEventType.TEXT
    assert event.text_preview == long_text[:200]
    assert event.text_full == long_text


def _antigravity_tool_step(
    step_index: int,
    state: str,
    tool_name: str = "view_file",
    parameters: dict[str, object] | None = None,
    conversation_id: str = "conv-1",
) -> str:
    return json.dumps(
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": conversation_id,
                "step_index": step_index,
                "state": state,
                "step_type": "tool",
                "tool_name": tool_name,
                "tool_info": {"name": tool_name, "parameters": parameters or {}},
            },
        }
    )


def test_antigravity_tool_start_events_have_step_tool_id() -> None:
    # Arrange
    parser = StreamParser(StreamFormat.ANTIGRAVITY)
    line = _antigravity_tool_step(4, "ACTIVE", "run_command", {"CommandLine": "make test"})

    # Act
    events = parser.parse_line(line)

    # Assert
    assert [e.event_type for e in events] == [StreamEventType.TOOL_START]
    assert events[0].tool_id == "conv-1:4"
    assert events[0].tool_detail == "$ make test"


def test_antigravity_tool_done_event_references_matching_tool_id() -> None:
    # Arrange
    parser = StreamParser(StreamFormat.ANTIGRAVITY)

    # Act
    start_events = parser.parse_line(_antigravity_tool_step(2, "ACTIVE"))
    done_events = parser.parse_line(_antigravity_tool_step(2, "DONE"))

    # Assert
    assert [e.event_type for e in done_events] == [StreamEventType.TOOL_DONE]
    assert done_events[0].tool_id == start_events[0].tool_id == "conv-1:2"
    assert done_events[0].tool_status == "done"


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


# ---- WP-25: per-format wire models — new regression tests ----


def test_antigravity_overlapping_tools_pair_by_step_not_order() -> None:
    """Tools finishing out of order still pair with their own start."""
    parser = StreamParser(StreamFormat.ANTIGRAVITY)
    parser.parse_line(_antigravity_tool_step(1, "ACTIVE", "view_file"))
    parser.parse_line(_antigravity_tool_step(2, "ACTIVE", "grep_search"))

    second_done = parser.parse_line(_antigravity_tool_step(2, "DONE", "grep_search"))
    first_done = parser.parse_line(_antigravity_tool_step(1, "DONE", "view_file"))

    assert second_done[0].tool_id == "conv-1:2"
    assert second_done[0].tool_name == "grep_search"
    assert first_done[0].tool_id == "conv-1:1"
    assert first_done[0].tool_name == "view_file"


def test_antigravity_subagent_steps_do_not_collide_with_parent_steps() -> None:
    """Step indexes restart per conversation; ids must stay distinct."""
    parser = StreamParser(StreamFormat.ANTIGRAVITY)

    parent = parser.parse_line(_antigravity_tool_step(2, "ACTIVE", conversation_id="parent"))
    child = parser.parse_line(_antigravity_tool_step(2, "ACTIVE", conversation_id="child"))

    assert parent[0].event_type is StreamEventType.TOOL_START
    assert child[0].event_type is StreamEventType.TOOL_START
    assert parent[0].tool_id != child[0].tool_id


def test_antigravity_repeated_active_update_is_progress_not_a_new_tool() -> None:
    parser = StreamParser(StreamFormat.ANTIGRAVITY)
    parser.parse_line(_antigravity_tool_step(3, "ACTIVE", "run_command"))

    events = parser.parse_line(_antigravity_tool_step(3, "ACTIVE", "run_command"))

    assert [e.event_type for e in events] == [StreamEventType.TOOL_EXEC]
    assert events[0].tool_id == "conv-1:3"


def test_antigravity_tool_finished_without_active_update_starts_and_finishes() -> None:
    parser = StreamParser(StreamFormat.ANTIGRAVITY)

    events = parser.parse_line(_antigravity_tool_step(5, "DONE", "list_dir"))

    assert [e.event_type for e in events] == [
        StreamEventType.TOOL_START,
        StreamEventType.TOOL_DONE,
    ]
    assert events[0].tool_id == events[1].tool_id == "conv-1:5"


def test_antigravity_failed_tool_reports_error_status_and_message() -> None:
    parser = StreamParser(StreamFormat.ANTIGRAVITY)
    parser.parse_line(_antigravity_tool_step(2, "ACTIVE", "run_command"))
    line = json.dumps(
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-1",
                "step_index": 2,
                "state": "ERROR",
                "step_type": "tool",
                "tool_name": "run_command",
                "tool_info": {
                    "name": "run_command",
                    "error": {"type": "TOOL_ERROR", "message": "user denied permission"},
                },
            },
        }
    )

    events = parser.parse_line(line)

    assert [e.event_type for e in events] == [StreamEventType.TOOL_DONE]
    assert events[0].tool_status == "error"
    assert events[0].error == "user denied permission"


def test_antigravity_quota_error_result_emits_rate_limit_after_result() -> None:
    parser = StreamParser(StreamFormat.ANTIGRAVITY)
    error = "RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 2h39m52s."
    line = json.dumps({"event": "result", "result": {"status": "ERROR", "error": error}})

    events = parser.parse_line(line)

    assert [e.event_type for e in events] == [
        StreamEventType.RESULT,
        StreamEventType.RATE_LIMIT,
    ]
    assert events[0].error == error
    assert events[1].error == error
    assert events[1].error_category == "rate_limit"


@pytest.mark.parametrize(
    ("result", "expected_error"),
    [
        pytest.param({"status": "SUCCESS"}, None, id="success"),
        pytest.param({}, None, id="no-status"),
        pytest.param({"status": "ERROR", "error": "boom"}, "boom", id="error-text"),
        pytest.param(
            {"status": "CANCELLED"},
            "antigravity reported status CANCELLED",
            id="status-only",
        ),
    ],
)
def test_antigravity_result_error_follows_status(
    result: dict[str, object], expected_error: str | None
) -> None:
    parser = StreamParser(StreamFormat.ANTIGRAVITY)

    events = parser.parse_line(json.dumps({"event": "result", "result": result}))

    assert [e.event_type for e in events] == [StreamEventType.RESULT]
    assert events[0].error == expected_error


def test_antigravity_denied_actions_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    parser = StreamParser(StreamFormat.ANTIGRAVITY)
    line = json.dumps(
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "denied_actions": [{"action": "write_file", "display_name": "WriteToFile"}],
            },
        }
    )

    with caplog.at_level(logging.INFO, logger="orchcore.stream.formats.antigravity"):
        events = parser.parse_line(line)

    assert events[0].error is None
    assert "denied 1 tool action(s)" in caplog.text


@pytest.mark.parametrize(
    ("step_update", "expected_type", "expected_error"),
    [
        pytest.param(
            {"step_type": "error_message", "message": "Agent execution terminated due to error."},
            StreamEventType.ERROR,
            "Agent execution terminated due to error.",
            id="error-message-step",
        ),
        pytest.param(
            {"step_type": "agent_response", "state": "ERROR", "error": {"message": "quota"}},
            StreamEventType.RATE_LIMIT,
            "quota",
            id="agent-response-quota",
        ),
        pytest.param(
            {"step_type": "agent_response", "state": "ERROR"},
            StreamEventType.ERROR,
            "antigravity step failed",
            id="agent-response-no-text",
        ),
    ],
)
def test_antigravity_failed_steps_classify_errors(
    step_update: dict[str, object],
    expected_type: StreamEventType,
    expected_error: str,
) -> None:
    parser = StreamParser(StreamFormat.ANTIGRAVITY)

    events = parser.parse_line(json.dumps({"event": "step_update", "step_update": step_update}))

    assert [e.event_type for e in events] == [expected_type]
    assert events[0].error == expected_error


@pytest.mark.parametrize(
    ("tool_name", "parameters", "expected_detail"),
    [
        pytest.param("view_file", {"AbsolutePath": "/repo/a.py"}, "/repo/a.py", id="view-file"),
        pytest.param("write_to_file", {"TargetFile": "/repo/b.py"}, "/repo/b.py", id="write"),
        pytest.param("run_command", {"CommandLine": "ls"}, "$ ls", id="run-command"),
        pytest.param("run_command", {}, None, id="run-command-without-command"),
        pytest.param("search_web", {"Query": "orchcore"}, "orchcore", id="search-web"),
        pytest.param("mcp__exa__search", {"q": 1}, "MCP mcp__exa__search", id="mcp-fallback"),
        pytest.param("finish", {}, None, id="no-parameters"),
    ],
)
def test_antigravity_tool_detail_reads_pascal_case_parameters(
    tool_name: str,
    parameters: dict[str, object],
    expected_detail: str | None,
) -> None:
    parser = StreamParser(StreamFormat.ANTIGRAVITY)

    events = parser.parse_line(_antigravity_tool_step(1, "ACTIVE", tool_name, parameters))

    assert events[0].tool_detail == expected_detail


def test_antigravity_invalid_duration_is_dropped() -> None:
    parser = StreamParser(StreamFormat.ANTIGRAVITY)
    line = json.dumps(
        {"event": "result", "result": {"status": "SUCCESS", "duration_seconds": "NaN"}}
    )

    events = parser.parse_line(line)

    assert events[0].duration_ms is None


def test_wire_validation_failure_is_counted_not_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Well-formed JSON that fails the wire schema increments its own counter."""
    parser = StreamParser(StreamFormat.CLAUDE)
    line = json.dumps({"type": "assistant", "message": "not-an-object"})

    with caplog.at_level("WARNING"):
        events = parser.parse_line(line)

    assert events == []
    assert parser.wire_validation_error_count == 1
    assert parser.json_parse_error_count == 0  # JSON-syntax meaning unchanged
    assert "Wire-schema validation failed" in caplog.text


def test_antigravity_non_string_event_discriminator_is_counted() -> None:
    parser = StreamParser(StreamFormat.ANTIGRAVITY)

    events = parser.parse_line(json.dumps({"event": 7, "result": {"status": "SUCCESS"}}))

    assert events == []
    assert parser.wire_validation_error_count == 1


def test_wire_validation_warnings_are_bounded_and_parser_recovers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    parser = StreamParser(StreamFormat.CLAUDE)
    bad_line = json.dumps({"type": None})
    valid_line = json.dumps({"type": "system", "subtype": "init", "session_id": "sess-789"})

    with caplog.at_level("WARNING"):
        for _ in range(5):
            assert parser.parse_line(bad_line) == []
        recovered_events = parser.parse_line(valid_line)

    assert parser.wire_validation_error_count == 5
    assert caplog.text.count("Wire-schema validation failed") == 3
    assert "Suppressing further wire-schema validation warnings" in caplog.text
    assert [event.event_type for event in recovered_events] == [StreamEventType.INIT]
    assert recovered_events[0].session_id == "sess-789"


@pytest.mark.parametrize(
    ("stream_format", "line_obj", "expected_type"),
    [
        pytest.param(
            StreamFormat.CLAUDE,
            {"type": "system", "subtype": "init", "session_id": "s", "future_field": {"x": 1}},
            StreamEventType.INIT,
            id="claude",
        ),
        pytest.param(
            StreamFormat.CODEX,
            {"type": "thread.started", "thread_id": "t", "future_field": [1, 2]},
            StreamEventType.INIT,
            id="codex",
        ),
        pytest.param(
            StreamFormat.COPILOT,
            {"text": "hi", "future_field": True},
            StreamEventType.INIT,
            id="copilot",
        ),
        pytest.param(
            StreamFormat.OPENCODE,
            {"type": "step_start", "future_field": "v"},
            StreamEventType.INIT,
            id="opencode",
        ),
        pytest.param(
            StreamFormat.ANTIGRAVITY,
            {"event": "init", "conversation_id": "c", "future_field": 7},
            StreamEventType.INIT,
            id="antigravity",
        ),
    ],
)
def test_unknown_fields_flow_through_raw_forward_compat(
    stream_format: StreamFormat,
    line_obj: dict[str, object],
    expected_type: StreamEventType,
) -> None:
    """extra="allow" forward compatibility: unknown wire fields must not break
    parsing, and the original object (unknown fields included) reaches raw."""
    events = StreamParser(stream_format).parse_line(json.dumps(line_obj))

    assert events[0].event_type == expected_type
    assert events[0].raw is not None
    assert events[0].raw["future_field"] == line_obj["future_field"]


def test_antigravity_failed_tool_accepts_plain_string_error() -> None:
    parser = StreamParser(StreamFormat.ANTIGRAVITY)
    line = json.dumps(
        {
            "event": "step_update",
            "step_update": {
                "step_index": 1,
                "state": "ERROR",
                "step_type": "tool",
                "tool_name": "run_command",
                "tool_info": {"error": "command timed out"},
            },
        }
    )

    events = parser.parse_line(line)

    assert events[-1].tool_status == "error"
    assert events[-1].error == "command timed out"


def test_antigravity_subagent_preview_is_bounded() -> None:
    parser = StreamParser(StreamFormat.ANTIGRAVITY)
    line = json.dumps(
        {
            "event": "step_update",
            "step_update": {
                "step_index": 1,
                "state": "ACTIVE",
                "step_type": "tool",
                "tool_name": "invoke_subagent",
                "subagent_info": {"subagents": [{"role": "r" * 500}, "ignored"]},
            },
        }
    )

    events = parser.parse_line(line)

    assert events[1].event_type is StreamEventType.SUBAGENT
    assert events[1].text_preview is not None
    assert len(events[1].text_preview) == 200


@pytest.mark.parametrize(
    "subagent_info",
    [
        pytest.param({"subagents": ["not-an-object"]}, id="non-object-subagent"),
        pytest.param({"subagents": []}, id="no-subagents"),
        pytest.param("not-an-object", id="non-object-info"),
    ],
)
def test_antigravity_subagent_preview_falls_back_without_usable_info(
    subagent_info: object,
) -> None:
    parser = StreamParser(StreamFormat.ANTIGRAVITY)
    line = json.dumps(
        {
            "event": "step_update",
            "step_update": {
                "step_index": 1,
                "state": "ACTIVE",
                "step_type": "tool",
                "tool_name": "invoke_subagent",
                "subagent_info": subagent_info,
            },
        }
    )

    events = parser.parse_line(line)

    assert events[1].event_type is StreamEventType.SUBAGENT
    assert events[1].text_preview == "Subagent"
