from __future__ import annotations

import json
import os
from decimal import Decimal
from types import NoneType
from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from orchcore.stream.events import StreamEvent, StreamEventType, StreamFormat
from orchcore.stream.filter import StreamFilter
from orchcore.stream.parser import StreamParser

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

settings.register_profile(
    "orchcore-ci",
    suppress_health_check=[HealthCheck.too_slow],
)
if os.getenv("CI"):
    settings.load_profile("orchcore-ci")

ALL_STREAM_FORMATS: tuple[StreamFormat, ...] = tuple(StreamFormat)
VALID_TOOL_STATUSES: frozenset[str] = frozenset({"running", "done", "error"})
KNOWN_TOOL_NAMES: tuple[str, ...] = (
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
    "Agent",
    "ls",
    "web_search_exa",
    "mcp__exa__web_search_exa",
    "mcp__exa__deep_search_exa",
)

SHORT_TEXT_STRATEGY: SearchStrategy[str] = st.text(max_size=40)
MEDIUM_TEXT_STRATEGY: SearchStrategy[str] = st.text(max_size=120)
LONG_TEXT_STRATEGY: SearchStrategy[str] = st.text(max_size=320)
NUMERIC_VALUE_STRATEGY: SearchStrategy[object] = st.one_of(
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
)
JSON_SCALAR_STRATEGY: SearchStrategy[object] = st.one_of(
    st.none(),
    st.booleans(),
    NUMERIC_VALUE_STRATEGY,
    MEDIUM_TEXT_STRATEGY,
)
JSON_VALUE_STRATEGY: SearchStrategy[object] = st.recursive(
    JSON_SCALAR_STRATEGY,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(SHORT_TEXT_STRATEGY, children, max_size=4),
    ),
    max_leaves=12,
)
JSON_DICT_STRATEGY: SearchStrategy[dict[str, object]] = st.dictionaries(
    SHORT_TEXT_STRATEGY,
    JSON_SCALAR_STRATEGY,
    max_size=6,
)
HELPER_INPUT_SCALAR_STRATEGY: SearchStrategy[object] = st.from_type(
    str | int | float | bool | NoneType
)
DECIMAL_INPUT_STRATEGY: SearchStrategy[object] = st.one_of(
    HELPER_INPUT_SCALAR_STRATEGY,
    st.lists(HELPER_INPUT_SCALAR_STRATEGY, max_size=4),
    st.dictionaries(SHORT_TEXT_STRATEGY, HELPER_INPUT_SCALAR_STRATEGY, max_size=4),
)


def _to_json_line(value: object) -> str:
    return json.dumps(value)


ARBITRARY_JSON_LINE_STRATEGY: SearchStrategy[str] = JSON_VALUE_STRATEGY.map(_to_json_line)
ARBITRARY_JSON_DICT_LINE_STRATEGY: SearchStrategy[str] = JSON_DICT_STRATEGY.map(_to_json_line)


@st.composite
def tool_input_strategy(draw: st.DrawFn) -> dict[str, str]:
    """Generate small tool-input mappings for helper and parser strategies."""

    tool_input = draw(
        st.dictionaries(
            SHORT_TEXT_STRATEGY,
            st.text(max_size=200),
            max_size=4,
        )
    )
    if draw(st.booleans()):
        tool_input[draw(st.sampled_from(("file_path", "filename", "pattern")))] = draw(
            st.text(max_size=200)
        )
    if draw(st.booleans()):
        tool_input[draw(st.sampled_from(("command", "description", "query", "path")))] = draw(
            st.text(max_size=200)
        )
    return tool_input


@st.composite
def claude_valid_line(draw: st.DrawFn) -> str:
    """Generate structurally valid Claude JSONL events with varied nested values."""

    event_type = draw(
        st.sampled_from(
            ("system", "content_block_start", "content_block_delta", "assistant", "result")
        )
    )
    data: dict[str, object] = {"type": event_type}

    if event_type == "system":
        data["subtype"] = draw(st.sampled_from(("init", "api_retry", "rate_limit", "unknown_sub")))
        if draw(st.booleans()):
            data["session_id"] = draw(st.one_of(st.none(), MEDIUM_TEXT_STRATEGY))
        if draw(st.booleans()):
            data["attempt"] = draw(
                st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY, st.none())
            )
        if draw(st.booleans()):
            data["max_retries"] = draw(
                st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY, st.none())
            )
        if draw(st.booleans()):
            data["delay"] = draw(st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY, st.none()))
        if draw(st.booleans()):
            data["retry_after_ms"] = draw(
                st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY, st.none())
            )
        if draw(st.booleans()):
            data["error_code"] = draw(
                st.one_of(
                    SHORT_TEXT_STRATEGY,
                    st.sampled_from(("rate_limit", "429", "server_error", "")),
                    st.none(),
                )
            )
    elif event_type == "content_block_start":
        block_type = draw(st.sampled_from(("thinking", "text", "tool_use", "unknown_block")))
        content_block: dict[str, object] = {"type": block_type}
        if draw(st.booleans()):
            content_block["id"] = draw(st.one_of(st.none(), SHORT_TEXT_STRATEGY))
        if block_type == "tool_use":
            content_block["name"] = draw(
                st.one_of(st.sampled_from(KNOWN_TOOL_NAMES), SHORT_TEXT_STRATEGY, st.none())
            )
            if draw(st.booleans()):
                content_block["input"] = draw(st.one_of(st.none(), tool_input_strategy()))
        data["content_block"] = content_block
    elif event_type == "content_block_delta":
        data["delta"] = {
            "type": draw(st.sampled_from(("input_json_delta", "text_delta", "unknown_delta")))
        }
    elif event_type == "assistant":
        content_items: list[dict[str, object]] = []
        for _ in range(draw(st.integers(min_value=0, max_value=3))):
            item_type = draw(st.sampled_from(("tool_use", "text", "unknown_item")))
            item: dict[str, object] = {"type": item_type}
            if item_type == "tool_use":
                item["id"] = draw(st.one_of(st.none(), SHORT_TEXT_STRATEGY))
                item["name"] = draw(
                    st.one_of(st.sampled_from(KNOWN_TOOL_NAMES), SHORT_TEXT_STRATEGY, st.none())
                )
                if draw(st.booleans()):
                    item["input"] = draw(st.one_of(st.none(), tool_input_strategy()))
            elif item_type == "text":
                item["text"] = draw(LONG_TEXT_STRATEGY)
            content_items.append(item)
        data["message"] = {"content": content_items}
    else:
        if draw(st.booleans()):
            data["total_cost_usd"] = draw(
                st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY, st.none())
            )
        if draw(st.booleans()):
            data["duration_ms"] = draw(
                st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY, st.none())
            )
        if draw(st.booleans()):
            data["exit_code"] = draw(
                st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY, st.none())
            )
        if draw(st.booleans()):
            data["num_turns"] = draw(
                st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY, st.none())
            )
        if draw(st.booleans()):
            data["session_id"] = draw(st.one_of(st.none(), MEDIUM_TEXT_STRATEGY))
        if draw(st.booleans()):
            data["usage"] = draw(
                st.dictionaries(
                    SHORT_TEXT_STRATEGY,
                    st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY),
                    max_size=4,
                )
            )
        if draw(st.booleans()):
            data["error"] = draw(
                st.one_of(
                    st.none(),
                    SHORT_TEXT_STRATEGY,
                    st.dictionaries(SHORT_TEXT_STRATEGY, SHORT_TEXT_STRATEGY, max_size=3),
                )
            )

    return _to_json_line(data)


@st.composite
def codex_valid_line(draw: st.DrawFn) -> str:
    """Generate structurally valid Codex JSONL events across supported shapes."""

    event_type = draw(
        st.sampled_from(
            (
                "thread.started",
                "item.started",
                "item.completed",
                "turn.completed",
                "response.output_item.delta",
                "error",
            )
        )
    )
    data: dict[str, object] = {"type": event_type}

    if event_type == "thread.started":
        data[draw(st.sampled_from(("session_id", "thread_id", "conversation_id")))] = draw(
            st.one_of(st.none(), MEDIUM_TEXT_STRATEGY)
        )
    elif event_type == "item.started":
        item: dict[str, object] = {
            "type": draw(
                st.sampled_from(("function_call", "agent_message", "agent_runner", "custom_agent"))
            )
        }
        if draw(st.booleans()):
            item["id"] = draw(st.one_of(st.none(), SHORT_TEXT_STRATEGY))
        if draw(st.booleans()):
            item["description"] = draw(MEDIUM_TEXT_STRATEGY)
        data["item"] = item
    elif event_type == "item.completed":
        item_type = draw(
            st.sampled_from(("agent_message", "function_call", "agent_runner", "custom_agent"))
        )
        item = {"type": item_type}
        if draw(st.booleans()):
            item["id"] = draw(st.one_of(st.none(), SHORT_TEXT_STRATEGY))
        if item_type == "agent_message":
            if draw(st.booleans()):
                item["content"] = draw(LONG_TEXT_STRATEGY)
            else:
                content_parts: list[dict[str, object]] = []
                for _ in range(draw(st.integers(min_value=0, max_value=3))):
                    part_type = draw(st.sampled_from(("output_text", "other")))
                    part: dict[str, object] = {"type": part_type}
                    part["text"] = draw(
                        LONG_TEXT_STRATEGY if part_type == "output_text" else SHORT_TEXT_STRATEGY
                    )
                    content_parts.append(part)
                item["content"] = content_parts
        data["item"] = item
    elif event_type == "turn.completed":
        if draw(st.booleans()):
            data["exit_code"] = draw(
                st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY, st.none())
            )
        if draw(st.booleans()):
            data["error"] = draw(
                st.one_of(
                    st.none(),
                    SHORT_TEXT_STRATEGY,
                    st.dictionaries(SHORT_TEXT_STRATEGY, SHORT_TEXT_STRATEGY, max_size=3),
                )
            )
        if draw(st.booleans()):
            data["usage"] = draw(
                st.dictionaries(
                    SHORT_TEXT_STRATEGY,
                    st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY),
                    max_size=4,
                )
            )
    elif event_type == "response.output_item.delta":
        if draw(st.booleans()):
            data["delta"] = draw(SHORT_TEXT_STRATEGY)
    else:
        if draw(st.booleans()):
            data["code"] = draw(
                st.sampled_from(("rate_limit_exceeded", "429", "internal_error", "boom"))
            )
        if draw(st.booleans()):
            data["message"] = draw(
                st.one_of(
                    st.none(),
                    SHORT_TEXT_STRATEGY,
                    st.dictionaries(SHORT_TEXT_STRATEGY, SHORT_TEXT_STRATEGY, max_size=3),
                )
            )
        if draw(st.booleans()):
            data["error"] = draw(
                st.one_of(
                    st.none(),
                    SHORT_TEXT_STRATEGY,
                    st.dictionaries(SHORT_TEXT_STRATEGY, SHORT_TEXT_STRATEGY, max_size=3),
                )
            )
        if draw(st.booleans()):
            data["retry_after_ms"] = draw(
                st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY, st.none())
            )
        if draw(st.booleans()):
            data["exit_code"] = draw(
                st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY, st.none())
            )

    return _to_json_line(data)


@st.composite
def gemini_function_call_line(draw: st.DrawFn) -> str:
    """Generate a Gemini functionCall line that always exercises tool-start parsing."""

    data: dict[str, object] = {
        "functionCall": {
            "name": draw(
                st.one_of(st.sampled_from(KNOWN_TOOL_NAMES), SHORT_TEXT_STRATEGY, st.none())
            ),
            "args": draw(st.one_of(st.none(), tool_input_strategy())),
        }
    }
    return _to_json_line(data)


@st.composite
def gemini_valid_line(draw: st.DrawFn) -> str:
    """Generate structurally valid Gemini JSONL events for tool, result, and error flows."""

    variant = draw(
        st.sampled_from(
            ("function_call", "tool_calls", "function_response", "result", "error", "unknown")
        )
    )
    data: dict[str, object] = {}

    if variant == "function_call":
        return draw(gemini_function_call_line())

    if variant == "tool_calls":
        tool_calls: list[dict[str, object]] = []
        for _ in range(draw(st.integers(min_value=1, max_value=3))):
            tool_calls.append(
                {
                    "name": draw(
                        st.one_of(st.sampled_from(KNOWN_TOOL_NAMES), SHORT_TEXT_STRATEGY, st.none())
                    ),
                    "args": draw(st.one_of(st.none(), tool_input_strategy())),
                }
            )
        data["tool_calls"] = tool_calls
        return _to_json_line(data)

    if variant == "function_response":
        data["functionResponse"] = {
            "name": draw(
                st.one_of(st.sampled_from(KNOWN_TOOL_NAMES), SHORT_TEXT_STRATEGY, st.none())
            ),
            "response": draw(JSON_VALUE_STRATEGY),
        }
        return _to_json_line(data)

    if variant == "result":
        candidate: dict[str, object] = {
            "content": {"parts": [{"text": draw(LONG_TEXT_STRATEGY)}]},
            "finishReason": draw(st.sampled_from(("STOP", "MAX_TOKENS", "OTHER"))),
        }
        data["candidates"] = [candidate]
        if draw(st.booleans()):
            data["usageMetadata"] = draw(
                st.dictionaries(
                    SHORT_TEXT_STRATEGY,
                    st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY),
                    max_size=4,
                )
            )
        return _to_json_line(data)

    if variant == "error":
        data["error"] = {
            "code": draw(st.one_of(st.integers(min_value=0, max_value=500), SHORT_TEXT_STRATEGY)),
            "status": draw(
                st.sampled_from(("RESOURCE_EXHAUSTED", "UNKNOWN", "FAILED_PRECONDITION"))
            ),
            "message": draw(MEDIUM_TEXT_STRATEGY),
        }
        if draw(st.booleans()):
            data["retry_after_ms"] = draw(
                st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY, st.none())
            )
        return _to_json_line(data)

    data.update(draw(JSON_DICT_STRATEGY))
    return _to_json_line(data)


@st.composite
def copilot_valid_line(draw: st.DrawFn) -> str:
    """Generate structurally valid Copilot JSONL events with tool and text variants."""

    data: dict[str, object] = {}
    if draw(st.booleans()):
        key = draw(st.sampled_from(("sessionId", "session_id", "conversationId", "threadId")))
        data[key] = draw(st.one_of(st.none(), MEDIUM_TEXT_STRATEGY))

    variant = draw(st.sampled_from(("tool_start", "tool_done", "text", "message", "unknown")))
    if variant in {"tool_start", "tool_done"}:
        data[draw(st.sampled_from(("toolName", "tool")))] = draw(
            st.one_of(st.sampled_from(KNOWN_TOOL_NAMES), SHORT_TEXT_STRATEGY)
        )
        if draw(st.booleans()):
            data["id"] = draw(st.one_of(st.none(), SHORT_TEXT_STRATEGY))
        if draw(st.booleans()):
            data[draw(st.sampled_from(("parameters", "input", "args")))] = draw(
                st.one_of(st.none(), tool_input_strategy())
            )
        if variant == "tool_done":
            done_key = draw(st.sampled_from(("result", "output", "done")))
            data[done_key] = draw(
                st.one_of(
                    st.just(True),
                    SHORT_TEXT_STRATEGY,
                    st.dictionaries(SHORT_TEXT_STRATEGY, SHORT_TEXT_STRATEGY, max_size=2),
                )
            )
    elif variant == "text":
        data["text"] = draw(LONG_TEXT_STRATEGY)
    elif variant == "message":
        data[draw(st.sampled_from(("message", "content")))] = draw(LONG_TEXT_STRATEGY)
    else:
        data.update(draw(JSON_DICT_STRATEGY))

    return _to_json_line(data)


@st.composite
def opencode_valid_line(draw: st.DrawFn) -> str:
    """Generate structurally valid OpenCode JSONL events for each supported type."""

    event_type = draw(st.sampled_from(("step_start", "tool_use", "text", "step_finish")))
    data: dict[str, object] = {"type": event_type}

    if event_type == "tool_use":
        data[draw(st.sampled_from(("tool", "name")))] = draw(
            st.one_of(st.sampled_from(KNOWN_TOOL_NAMES), SHORT_TEXT_STRATEGY, st.none())
        )
        if draw(st.booleans()):
            data["id"] = draw(st.one_of(st.none(), SHORT_TEXT_STRATEGY))
        if draw(st.booleans()):
            data["input"] = draw(st.one_of(st.none(), tool_input_strategy()))
        if draw(st.booleans()):
            data["result"] = draw(JSON_VALUE_STRATEGY)
    elif event_type == "text":
        data["part"] = {"text": draw(LONG_TEXT_STRATEGY)}
    elif event_type == "step_finish":
        if draw(st.booleans()):
            data["exit_code"] = draw(
                st.one_of(NUMERIC_VALUE_STRATEGY, SHORT_TEXT_STRATEGY, st.none())
            )
        if draw(st.booleans()):
            data["error"] = draw(
                st.one_of(
                    st.none(),
                    SHORT_TEXT_STRATEGY,
                    st.dictionaries(SHORT_TEXT_STRATEGY, SHORT_TEXT_STRATEGY, max_size=3),
                )
            )

    return _to_json_line(data)


ANY_VALID_LINE_STRATEGY: SearchStrategy[str] = st.one_of(
    claude_valid_line(),
    codex_valid_line(),
    gemini_valid_line(),
    copilot_valid_line(),
    opencode_valid_line(),
)


def _assert_stream_event_list(events: list[StreamEvent]) -> None:
    assert isinstance(events, list)
    for event in events:
        assert isinstance(event, StreamEvent)
        assert isinstance(event.event_type, StreamEventType)


# ---- Category 1: Universal Crash Resistance ----


@pytest.mark.parametrize("stream_format", ALL_STREAM_FORMATS)
@given(line=st.text())
def test_parse_line_never_crashes_on_arbitrary_strings(
    stream_format: StreamFormat,
    line: str,
) -> None:
    """Parsing arbitrary Unicode strings must never raise an exception."""

    events = StreamParser(stream_format).parse_line(line)

    assert isinstance(events, list)


@pytest.mark.parametrize("stream_format", ALL_STREAM_FORMATS)
@given(line=ARBITRARY_JSON_LINE_STRATEGY)
def test_parse_line_never_crashes_on_arbitrary_json(
    stream_format: StreamFormat,
    line: str,
) -> None:
    """Parsing any JSON-serializable payload must never raise an exception."""

    events = StreamParser(stream_format).parse_line(line)

    assert isinstance(events, list)


@pytest.mark.parametrize("stream_format", ALL_STREAM_FORMATS)
@given(line=ARBITRARY_JSON_DICT_LINE_STRATEGY)
def test_parse_line_never_crashes_on_arbitrary_json_dicts(
    stream_format: StreamFormat,
    line: str,
) -> None:
    """Parsing arbitrary JSON objects must always return a StreamEvent list."""

    events = StreamParser(stream_format).parse_line(line)

    _assert_stream_event_list(events)


# ---- Category 2: Output Invariants ----


@pytest.mark.parametrize("stream_format", ALL_STREAM_FORMATS)
@given(line=ANY_VALID_LINE_STRATEGY)
def test_all_returned_events_are_valid_stream_events(
    stream_format: StreamFormat,
    line: str,
) -> None:
    """Every parsed event must be a StreamEvent with a valid StreamEventType."""

    events = StreamParser(stream_format).parse_line(line)

    _assert_stream_event_list(events)


@pytest.mark.parametrize("stream_format", ALL_STREAM_FORMATS)
@given(line=ANY_VALID_LINE_STRATEGY)
def test_text_preview_never_exceeds_200_chars(
    stream_format: StreamFormat,
    line: str,
) -> None:
    """Any preview emitted by the parser must be truncated to at most 200 characters."""

    events = StreamParser(stream_format).parse_line(line)

    assert all(len(event.text_preview) <= 200 for event in events if event.text_preview is not None)


@pytest.mark.parametrize("stream_format", ALL_STREAM_FORMATS)
@given(line=ANY_VALID_LINE_STRATEGY)
def test_tool_status_is_valid_literal(
    stream_format: StreamFormat,
    line: str,
) -> None:
    """Any populated tool status must stay within the supported literal values."""

    events = StreamParser(stream_format).parse_line(line)

    assert all(
        event.tool_status in VALID_TOOL_STATUSES
        for event in events
        if event.tool_status is not None
    )


@pytest.mark.parametrize("stream_format", ALL_STREAM_FORMATS)
@given(line=ANY_VALID_LINE_STRATEGY)
def test_tool_start_events_always_have_running_status(
    stream_format: StreamFormat,
    line: str,
) -> None:
    """Tool-start events must always advertise a running tool status."""

    events = StreamParser(stream_format).parse_line(line)

    assert all(
        event.tool_status == "running"
        for event in events
        if event.event_type == StreamEventType.TOOL_START
    )


@pytest.mark.parametrize("stream_format", ALL_STREAM_FORMATS)
@given(line=ANY_VALID_LINE_STRATEGY)
def test_tool_done_events_always_have_done_status(
    stream_format: StreamFormat,
    line: str,
) -> None:
    """Tool-done events must always advertise a done tool status."""

    events = StreamParser(stream_format).parse_line(line)

    assert all(
        event.tool_status == "done"
        for event in events
        if event.event_type == StreamEventType.TOOL_DONE
    )


# ---- Category 3: Format-Specific Strategies ----


@settings(max_examples=200)
@given(line=claude_valid_line())
def test_claude_parser_handles_random_valid_structures(line: str) -> None:
    """Claude parser must accept random valid Claude structures without crashing."""

    events = StreamParser(StreamFormat.CLAUDE).parse_line(line)

    _assert_stream_event_list(events)


@settings(max_examples=200)
@given(line=codex_valid_line())
def test_codex_parser_handles_random_valid_structures(line: str) -> None:
    """Codex parser must accept random valid Codex structures without crashing."""

    events = StreamParser(StreamFormat.CODEX).parse_line(line)

    _assert_stream_event_list(events)


@settings(max_examples=200)
@given(line=gemini_valid_line())
def test_gemini_parser_handles_random_valid_structures(line: str) -> None:
    """Gemini parser must accept random valid Gemini structures without crashing."""

    events = StreamParser(StreamFormat.GEMINI).parse_line(line)

    _assert_stream_event_list(events)


@settings(max_examples=200)
@given(line=copilot_valid_line())
def test_copilot_parser_handles_random_valid_structures(line: str) -> None:
    """Copilot parser must accept random valid Copilot structures without crashing."""

    events = StreamParser(StreamFormat.COPILOT).parse_line(line)

    _assert_stream_event_list(events)


@settings(max_examples=200)
@given(line=opencode_valid_line())
def test_opencode_parser_handles_random_valid_structures(line: str) -> None:
    """OpenCode parser must accept random valid OpenCode structures without crashing."""

    events = StreamParser(StreamFormat.OPENCODE).parse_line(line)

    _assert_stream_event_list(events)


# ---- Category 4: Helper Function Properties ----


@given(value=DECIMAL_INPUT_STRATEGY)
def test_to_decimal_never_crashes(value: object) -> None:
    """Decimal conversion must never raise and may only return Decimal or None."""

    result = StreamParser._to_decimal(value)

    assert result is None or isinstance(result, Decimal)


@given(
    tool_name=st.one_of(st.none(), st.sampled_from(KNOWN_TOOL_NAMES), SHORT_TEXT_STRATEGY),
    tool_input=st.one_of(st.none(), tool_input_strategy()),
)
def test_extract_tool_detail_never_crashes(
    tool_name: str | None,
    tool_input: dict[str, str] | None,
) -> None:
    """Tool-detail extraction must never raise on arbitrary names and small mappings."""

    result = StreamParser._extract_tool_detail(tool_name, tool_input)

    assert result is None or isinstance(result, str)


@given(
    tool_name=st.one_of(st.none(), st.sampled_from(KNOWN_TOOL_NAMES), SHORT_TEXT_STRATEGY),
    tool_input=st.one_of(st.none(), tool_input_strategy()),
)
def test_extract_tool_detail_output_bounded(
    tool_name: str | None,
    tool_input: dict[str, str] | None,
) -> None:
    """Extracted tool detail must stay within reasonable display bounds."""

    result = StreamParser._extract_tool_detail(tool_name, tool_input)

    if result is None:
        return

    assert len(result) < 500
    if tool_name and tool_name.lower() == "grep":
        assert len(result) <= 50
    if tool_name and tool_name.lower() == "bash":
        assert len(result) <= 80


# ---- Category 5: StreamFilter Properties ----


@pytest.mark.parametrize("stream_format", ALL_STREAM_FORMATS)
@given(line=st.text())
def test_filter_never_crashes_on_arbitrary_strings(
    stream_format: StreamFormat,
    line: str,
) -> None:
    """Filter matching must never raise and must always return a bool."""

    should_keep = StreamFilter(stream_format).should_keep(line)

    assert isinstance(should_keep, bool)


@pytest.mark.parametrize("stream_format", ALL_STREAM_FORMATS)
@given(line=st.from_regex(r"^\s*$", fullmatch=True))
def test_filter_always_drops_blank_lines(
    stream_format: StreamFormat,
    line: str,
) -> None:
    """Whitespace-only lines must always be dropped before parsing."""

    assert StreamFilter(stream_format).should_keep(line) is False


# ---- Category 6: Stateful Testing ----


@given(line=ARBITRARY_JSON_DICT_LINE_STRATEGY)
def test_copilot_first_object_always_emits_init(line: str) -> None:
    """The first parsed Copilot object must always emit an implicit INIT event."""

    events = StreamParser(StreamFormat.COPILOT).parse_line(line)

    assert any(event.event_type == StreamEventType.INIT for event in events)


@given(lines=st.lists(gemini_function_call_line(), min_size=1, max_size=10))
def test_gemini_tool_ids_are_sequential(lines: list[str]) -> None:
    """Gemini tool IDs must increment monotonically across function-call lines."""

    parser = StreamParser(StreamFormat.GEMINI)
    tool_start_events: list[StreamEvent] = []
    for line in lines:
        tool_start_events.extend(
            event
            for event in parser.parse_line(line)
            if event.event_type == StreamEventType.TOOL_START
        )

    assert len(tool_start_events) == len(lines)
    assert [event.tool_id for event in tool_start_events] == [
        f"gemini-tool-{index}" for index in range(1, len(lines) + 1)
    ]
