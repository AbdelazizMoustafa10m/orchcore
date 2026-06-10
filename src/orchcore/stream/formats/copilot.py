"""GitHub Copilot CLI wire format (WP-25, analysis/06 plan).

Copilot has no dedicated event-type discriminator and no init frame, so the
envelope is fully lenient (every field ``object``-typed): any JSON object
must parse — the first one seen becomes an implicit INIT event.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from orchcore.stream.events import StreamEvent, StreamEventType
from orchcore.stream.formats._shared import (
    _WireValidationError,
    dict_or_none,
    extract_tool_detail,
    first_str,
    str_or_none,
    validate_or_none,
)

logger: logging.Logger = logging.getLogger(__name__)


class _CopilotMetadata(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    sessionId: object = None
    session_id: object = None
    conversationId: object = None
    conversation_id: object = None
    threadId: object = None
    thread_id: object = None


class _CopilotEvent(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    toolName: object = None
    tool: object = None
    id: object = None
    parameters: object = None
    input: object = None
    args: object = None
    result: object = None
    output: object = None
    done: object = None
    text: object = None
    message: object = None
    content: object = None
    sessionId: object = None
    session_id: object = None
    conversationId: object = None
    conversation_id: object = None
    threadId: object = None
    thread_id: object = None
    metadata: object = None


def _session_id(event: _CopilotEvent) -> str | None:
    session_id = first_str(
        event.sessionId,
        event.session_id,
        event.conversationId,
        event.conversation_id,
        event.threadId,
        event.thread_id,
    )
    if session_id is not None:
        return session_id

    metadata = validate_or_none(_CopilotMetadata, event.metadata)
    if metadata is not None:
        return first_str(
            metadata.sessionId,
            metadata.session_id,
            metadata.conversationId,
            metadata.conversation_id,
            metadata.threadId,
            metadata.thread_id,
        )
    return None


class CopilotParser:
    """Stateful parser for Copilot JSONL objects (implicit INIT on first object)."""

    def __init__(self) -> None:
        self._init_seen = False

    def parse(self, data: dict[str, object]) -> list[StreamEvent]:
        """Parse one Copilot wire object into zero or more events."""
        try:
            event = _CopilotEvent.model_validate(data)
        except ValidationError as exc:  # pragma: no cover - fully lenient envelope
            raise _WireValidationError(str(exc)) from exc

        events: list[StreamEvent] = []

        if not self._init_seen:
            self._init_seen = True
            # Copilot does not emit a dedicated init frame, so the first object
            # becomes an implicit INIT event carrying available session metadata.
            events.append(
                StreamEvent(
                    event_type=StreamEventType.INIT,
                    session_id=_session_id(event),
                    raw=data,
                )
            )

        # Tool invocation: presence of "toolName" or "tool".
        tool_name = str_or_none(event.toolName or event.tool)
        if tool_name:
            tool_input = dict_or_none(event.parameters or event.input or event.args)
            is_done = bool(event.result or event.output or event.done)
            events.append(
                StreamEvent(
                    event_type=(
                        StreamEventType.TOOL_DONE if is_done else StreamEventType.TOOL_START
                    ),
                    tool_name=tool_name,
                    tool_id=str_or_none(event.id),
                    tool_detail=extract_tool_detail(tool_name, tool_input),
                    tool_status="done" if is_done else "running",
                    raw=data,
                )
            )
            return events

        # Text/message chunk.
        text_val = str_or_none(event.text or event.message or event.content)
        if text_val:
            events.append(
                StreamEvent(
                    event_type=StreamEventType.TEXT,
                    text_preview=text_val[:200],
                    text_full=text_val,
                    raw=data,
                )
            )
            return events

        logger.debug("Copilot: unrecognised object keys: %s", list(data.keys())[:10])
        return events
