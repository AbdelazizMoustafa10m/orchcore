"""OpenCode (``--format json``) wire format (WP-25, analysis/06 plan)."""

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
    validate_or_none,
)

logger: logging.Logger = logging.getLogger(__name__)


class _OpenCodePart(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    text: object = ""


class _OpenCodeEvent(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    type: str = ""
    tool: object = None
    name: object = None
    id: object = None
    input: object = None
    result: object = None
    part: object = None
    exit_code: object = None
    error: object = None


class OpenCodeParser:
    """Stateless parser for OpenCode JSONL objects."""

    def parse(self, data: dict[str, object]) -> list[StreamEvent]:
        """Parse one OpenCode wire object into zero or more events."""
        try:
            event = _OpenCodeEvent.model_validate(data)
        except ValidationError as exc:
            raise _WireValidationError(str(exc)) from exc

        match event.type:
            case "step_start":
                return [StreamEvent(event_type=StreamEventType.INIT, raw=data)]
            case "tool_use":
                return _parse_tool_use(event, data)
            case "text":
                part = validate_or_none(_OpenCodePart, event.part)
                text_val = str(part.text) if part is not None else ""
                if text_val:
                    return [
                        StreamEvent(
                            event_type=StreamEventType.TEXT,
                            text_preview=text_val[:200],
                            text_full=text_val,
                            raw=data,
                        )
                    ]
                return []
            case "step_finish":
                return [
                    StreamEvent(
                        event_type=StreamEventType.RESULT,
                        exit_code=int_or_none(event.exit_code),
                        error=error_text_or_none(event.error),
                        raw=data,
                    )
                ]
            case _:
                logger.debug("OpenCode: unknown event type %r", event.type)
                return []


def _parse_tool_use(event: _OpenCodeEvent, data: dict[str, object]) -> list[StreamEvent]:
    # Presence of "result" (even null) marks completion of the invocation.
    has_result = "result" in event.model_fields_set
    tool_name = str_or_none(event.tool or event.name)
    tool_input = dict_or_none(event.input)
    return [
        StreamEvent(
            event_type=StreamEventType.TOOL_DONE if has_result else StreamEventType.TOOL_START,
            tool_name=tool_name,
            tool_id=str_or_none(event.id),
            tool_detail=extract_tool_detail(tool_name, tool_input),
            tool_status="done" if has_result else "running",
            raw=data,
        )
    ]
