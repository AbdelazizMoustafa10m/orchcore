"""Codex (``codex exec --json``) wire format (WP-25, analysis/06 plan)."""

from __future__ import annotations

import logging
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from orchcore.stream.events import StreamEvent, StreamEventType
from orchcore.stream.formats._shared import (
    _WireValidationError,
    error_text_or_none,
    first_str,
    int_or_none,
    str_or_none,
    token_usage_or_none,
    validate_or_none,
)

logger: logging.Logger = logging.getLogger(__name__)


class _CodexItem(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    id: object = None
    type: object = ""
    description: object = None
    content: object = None


class _CodexPart(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    type: object = ""
    text: object = ""


class _CodexEvent(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    type: str = ""
    session_id: object = None
    thread_id: object = None
    conversation_id: object = None
    item: object = None
    usage: object = None
    exit_code: object = None
    error: object = None
    code: object = None
    message: object = None
    retry_after_ms: object = None


def _item_or_empty(value: object) -> _CodexItem:
    """Mirror the old ``item if isinstance(item, dict) else {}`` fallback."""
    return validate_or_none(_CodexItem, value) or _CodexItem()


class CodexParser:
    """Stateless parser for Codex JSONL objects."""

    def parse(self, data: dict[str, object]) -> list[StreamEvent]:
        """Parse one Codex wire object into zero or more events."""
        try:
            event = _CodexEvent.model_validate(data)
        except ValidationError as exc:
            raise _WireValidationError(str(exc)) from exc

        match event.type:
            case "thread.started":
                return [
                    StreamEvent(
                        event_type=StreamEventType.INIT,
                        session_id=first_str(
                            event.session_id, event.thread_id, event.conversation_id
                        ),
                        raw=data,
                    )
                ]
            case "item.started":
                return _parse_item_started(event, data)
            case "response.output_item.delta":
                # Output-item deltas signal tool execution in progress.
                return [
                    StreamEvent(
                        event_type=StreamEventType.TOOL_EXEC,
                        tool_status="running",
                        raw=data,
                    )
                ]
            case "item.completed":
                return _parse_item_completed(event, data)
            case "turn.completed":
                return [
                    StreamEvent(
                        event_type=StreamEventType.RESULT,
                        exit_code=int_or_none(event.exit_code),
                        error=error_text_or_none(event.error),
                        token_usage=token_usage_or_none(event.usage),
                        raw=data,
                    )
                ]
            case "error":
                return _parse_error(event, data)
            case _:
                logger.debug("Codex: unknown event type %r", event.type)
                return []


def _parse_item_started(event: _CodexEvent, data: dict[str, object]) -> list[StreamEvent]:
    item = _item_or_empty(event.item)
    item_type = str(item.type)
    events: list[StreamEvent] = [
        StreamEvent(
            event_type=StreamEventType.TOOL_START,
            tool_id=str_or_none(item.id),
            tool_name=str_or_none(item_type) if item_type else None,
            tool_status="running",
            raw=data,
        )
    ]
    # Emit SUBAGENT when a nested agent item starts.
    if "agent" in item_type.lower():
        events.append(
            StreamEvent(
                event_type=StreamEventType.SUBAGENT,
                text_preview=str_or_none(item.description or item_type),
                raw=data,
            )
        )
    return events


def _parse_item_completed(event: _CodexEvent, data: dict[str, object]) -> list[StreamEvent]:
    item = _item_or_empty(event.item)
    item_type = str(item.type)
    if item_type == "agent_message":
        text_val = ""
        if isinstance(item.content, list):
            for raw_part in item.content:
                part = validate_or_none(_CodexPart, raw_part)
                if part is not None and part.type == "output_text":
                    text_val = str(part.text)
                    break
        elif isinstance(item.content, str):
            text_val = item.content
        return [
            StreamEvent(
                event_type=StreamEventType.TEXT,
                text_preview=text_val[:200] if text_val else None,
                text_full=text_val or None,
                raw=data,
            )
        ]
    return [
        StreamEvent(
            event_type=StreamEventType.TOOL_DONE,
            tool_id=str_or_none(item.id),
            tool_name=item_type or None,
            tool_status="done",
            raw=data,
        )
    ]


def _parse_error(event: _CodexEvent, data: dict[str, object]) -> list[StreamEvent]:
    # Rate limits are signalled via the error event's code/message.
    error_code = str_or_none(event.code) or error_text_or_none(event.error)
    error_code_text = error_code or ""
    if "rate_limit" in error_code_text.lower() or "429" in error_code_text:
        return [
            StreamEvent(
                event_type=StreamEventType.RATE_LIMIT,
                retry_delay_ms=int_or_none(event.retry_after_ms),
                error_category=error_code_text or "rate_limit",
                raw=data,
            )
        ]
    return [
        StreamEvent(
            event_type=StreamEventType.ERROR,
            error=(
                error_text_or_none(event.message)
                or error_text_or_none(event.error)
                or error_code_text
            ),
            exit_code=int_or_none(event.exit_code),
            error_category=error_code_text or "error",
            raw=data,
        )
    ]
