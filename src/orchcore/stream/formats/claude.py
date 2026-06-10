"""Claude ``stream-json`` wire format (WP-25, analysis/06 plan).

Private Pydantic envelope models (``extra="allow"`` for forward
compatibility) replace the previous hand-navigated ``dict[str, Any]`` paths.
Scalar fields are typed ``object`` and coerced leniently because Claude CLI
versions vary in the types they emit; structural fields (``message``,
``content_block``, ``delta``) are nested models whose validation failures
surface through :class:`_WireValidationError`.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from orchcore.stream.events import StreamEvent, StreamEventType
from orchcore.stream.formats._shared import (
    _WireValidationError,
    dict_or_none,
    error_text_or_none,
    extract_tool_detail,
    int_or_none,
    str_or_none,
    to_decimal,
    token_usage_or_none,
    validate_or_none,
)

logger: logging.Logger = logging.getLogger(__name__)


class _ClaudeDelta(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    type: object = ""


class _ClaudeContentBlock(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    type: object = ""
    name: object = None
    id: object = None
    input: object = None


class _ClaudeContentItem(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    type: object = ""
    name: object = None
    id: object = None
    input: object = None
    text: object = ""


class _ClaudeMessage(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    content: object = None


class _ClaudeEvent(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    type: str = ""
    subtype: object = ""
    session_id: object = None
    attempt: object = None
    max_retries: object = None
    delay: object = None
    retry_after_ms: object = None
    error_code: object = None
    error: object = None
    message: _ClaudeMessage | None = None
    content_block: _ClaudeContentBlock | None = None
    delta: _ClaudeDelta | None = None
    usage: object = None
    total_cost_usd: object = None
    duration_ms: object = None
    exit_code: object = None
    num_turns: object = None


class ClaudeParser:
    """Stateless parser for Claude stream-json objects."""

    def parse(self, data: dict[str, object]) -> list[StreamEvent]:
        """Parse one Claude wire object into zero or more events."""
        try:
            event = _ClaudeEvent.model_validate(data)
        except ValidationError as exc:
            raise _WireValidationError(str(exc)) from exc

        match event.type:
            case "system":
                return _parse_system(event, data)
            case "content_block_start":
                return _parse_content_block_start(event, data)
            case "content_block_delta":
                # Tool-input streaming deltas signal TOOL_EXEC progress.
                return _parse_content_block_delta(event, data)
            case "assistant":
                return _parse_assistant(event, data)
            case "result":
                return _parse_result(event, data)
            case _:
                logger.debug("Claude: unknown event type %r", event.type)
                return []


def _parse_system(event: _ClaudeEvent, data: dict[str, object]) -> list[StreamEvent]:
    if event.subtype == "init":
        return [
            StreamEvent(
                event_type=StreamEventType.INIT,
                session_id=str_or_none(event.session_id),
                raw=data,
            )
        ]
    if event.subtype == "api_retry":
        events: list[StreamEvent] = [
            StreamEvent(
                event_type=StreamEventType.RETRY,
                retry_attempt=int_or_none(event.attempt),
                retry_max=int_or_none(event.max_retries),
                retry_delay_ms=int_or_none(event.delay),
                raw=data,
            )
        ]
        # Emit RATE_LIMIT when the retry is caused by a rate-limit error.
        error_code = str(event.error_code or event.error or "")
        if "rate_limit" in error_code.lower() or "429" in error_code:
            events.append(
                StreamEvent(
                    event_type=StreamEventType.RATE_LIMIT,
                    retry_delay_ms=int_or_none(event.delay),
                    error_category=error_code or "rate_limit",
                    raw=data,
                )
            )
        return events
    # Dedicated rate-limit subtype some Claude versions emit.
    if event.subtype == "rate_limit":
        return [
            StreamEvent(
                event_type=StreamEventType.RATE_LIMIT,
                retry_delay_ms=int_or_none(event.retry_after_ms or event.delay),
                error_category="rate_limit",
                raw=data,
            )
        ]
    logger.debug("Claude system: unknown subtype %r", event.subtype)
    return []


def _parse_content_block_delta(event: _ClaudeEvent, data: dict[str, object]) -> list[StreamEvent]:
    if event.delta is None:
        return []
    # input_json_delta signals that a tool's input is being streamed.
    if str(event.delta.type) == "input_json_delta":
        return [
            StreamEvent(
                event_type=StreamEventType.TOOL_EXEC,
                tool_status="running",
                raw=data,
            )
        ]
    return []


def _parse_content_block_start(event: _ClaudeEvent, data: dict[str, object]) -> list[StreamEvent]:
    block = event.content_block
    if block is None:
        return []
    block_type = str(block.type)
    if block_type == "thinking":
        return [
            StreamEvent(
                event_type=StreamEventType.STATE_CHANGE,
                text_preview="thinking",
                raw=data,
            )
        ]
    if block_type == "text":
        return [
            StreamEvent(
                event_type=StreamEventType.STATE_CHANGE,
                text_preview="writing",
                raw=data,
            )
        ]
    if block_type != "tool_use":
        return []
    tool_name = str_or_none(block.name)
    tool_input = dict_or_none(block.input)
    events: list[StreamEvent] = [
        StreamEvent(
            event_type=StreamEventType.TOOL_START,
            tool_name=tool_name,
            tool_id=str_or_none(block.id),
            tool_detail=extract_tool_detail(tool_name, tool_input),
            tool_status="running",
            raw=data,
        )
    ]
    # Emit SUBAGENT when an Agent tool is invoked.
    if tool_name and tool_name.lower() == "agent":
        subagent_desc = str(tool_input.get("description", "")) if tool_input else ""
        events.append(
            StreamEvent(
                event_type=StreamEventType.SUBAGENT,
                text_preview=f"Subagent: {subagent_desc}" if subagent_desc else "Subagent",
                raw=data,
            )
        )
    return events


def _parse_assistant(event: _ClaudeEvent, data: dict[str, object]) -> list[StreamEvent]:
    if event.message is None:
        return []
    content = event.message.content
    if not isinstance(content, list):
        return []

    events: list[StreamEvent] = []
    for raw_item in content:
        # Per-item validation mirrors the old per-item isinstance skip:
        # one malformed item must not drop its well-formed siblings.
        item = validate_or_none(_ClaudeContentItem, raw_item)
        if item is None:
            continue
        if item.type == "tool_use":
            tool_name = str_or_none(item.name)
            tool_input = dict_or_none(item.input)
            events.append(
                StreamEvent(
                    event_type=StreamEventType.TOOL_DONE,
                    tool_name=tool_name,
                    tool_id=str_or_none(item.id),
                    tool_detail=extract_tool_detail(tool_name, tool_input),
                    tool_status="done",
                    raw=data,
                )
            )
        elif item.type == "text":
            text = str(item.text)
            if text:
                events.append(
                    StreamEvent(
                        event_type=StreamEventType.TEXT,
                        text_preview=text[:200],
                        text_full=text,
                        raw=data,
                    )
                )
    return events


def _parse_result(event: _ClaudeEvent, data: dict[str, object]) -> list[StreamEvent]:
    return [
        StreamEvent(
            event_type=StreamEventType.RESULT,
            cost_usd=to_decimal(event.total_cost_usd),
            duration_ms=int_or_none(event.duration_ms),
            exit_code=int_or_none(event.exit_code),
            num_turns=int_or_none(event.num_turns),
            session_id=str_or_none(event.session_id),
            token_usage=token_usage_or_none(event.usage),
            error=error_text_or_none(event.error),
            raw=data,
        )
    ]
