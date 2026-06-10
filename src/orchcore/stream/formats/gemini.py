"""Gemini CLI wire format, best-effort (WP-25, analysis/06 plan).

Gemini lines have no type discriminator; recognition is by key presence. The
envelope is fully lenient so arbitrary objects fall through to the implicit
INIT/HEARTBEAT path instead of failing validation.

Tool correlation: Gemini emits no tool ids, so synthetic ``gemini-tool-N``
ids are generated per ``functionCall`` and completions are paired via a FIFO
of open ids — Gemini returns ``functionResponse`` frames in call order. This
replaces the pre-WP-25 global counter, which mis-attributed completions when
tool calls overlapped (finding C10).
"""

from __future__ import annotations

import logging
from collections import deque
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from orchcore.stream.events import StreamEvent, StreamEventType
from orchcore.stream.formats._shared import (
    _WireValidationError,
    dict_or_none,
    extract_tool_detail,
    int_or_none,
    str_or_none,
    validate_or_none,
)

logger: logging.Logger = logging.getLogger(__name__)


class _GeminiError(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    code: object = 0
    status: object = ""
    retry_after_ms: object = None


class _GeminiToolCall(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    name: object = None
    args: object = None


class _GeminiResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    name: object = None


class _GeminiContent(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    parts: object = None


class _GeminiPart(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    text: object = ""


class _GeminiCandidate(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    content: object = None
    finishReason: object = ""  # wire field name


class _GeminiEvent(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    error: object = None
    functionCall: object = None  # wire field name
    tool_calls: object = None
    functionResponse: object = None  # wire field name
    tool_response: object = None
    candidates: object = None
    usageMetadata: object = None  # wire field name
    retry_after_ms: object = None


class GeminiParser:
    """Stateful parser for Gemini JSONL objects (line counter + FIFO tool ids)."""

    def __init__(self) -> None:
        # Count parsed lines to emit periodic init/heartbeat pings on the
        # fallback path.
        self._line_count = 0
        # Monotonic source for synthetic tool ids.
        self._tool_seq = 0
        # FIFO of synthetic ids for tools started but not yet finished.
        self._open_tools: deque[str] = deque()
        # Orphaned functionResponse frames (no open tool) get their own ids.
        self._orphan_seq = 0

    def parse(self, data: dict[str, object]) -> list[StreamEvent]:
        """Parse one Gemini wire object into zero or more events."""
        try:
            event = _GeminiEvent.model_validate(data)
        except ValidationError as exc:  # pragma: no cover - fully lenient envelope
            raise _WireValidationError(str(exc)) from exc

        self._line_count += 1

        if "error" in event.model_fields_set:
            rate_limit_events = self._parse_error(event, data)
            if rate_limit_events:
                return rate_limit_events

        raw_calls = event.functionCall or event.tool_calls
        if raw_calls:
            return self._parse_tool_calls(raw_calls, data)

        if (
            "functionResponse" in event.model_fields_set
            or "tool_response" in event.model_fields_set
        ):
            return self._parse_tool_response(event, data)

        if "usageMetadata" in event.model_fields_set or "candidates" in event.model_fields_set:
            return self._parse_result_blob(event, data)

        # Fallback path — emit INIT on the first unrecognised line, then
        # periodic HEARTBEAT pings so the UI receives visible progress.
        logger.debug("Gemini: unrecognised object keys: %s", list(data.keys())[:10])
        if self._line_count == 1:
            return [StreamEvent(event_type=StreamEventType.INIT, raw=data)]
        if self._line_count % 10 == 0:
            return [
                StreamEvent(
                    event_type=StreamEventType.HEARTBEAT,
                    text_preview=f"Gemini processing (line {self._line_count})",
                    raw=data,
                )
            ]
        return []

    def _parse_error(self, event: _GeminiEvent, data: dict[str, object]) -> list[StreamEvent]:
        error = validate_or_none(_GeminiError, event.error)
        if error is None:
            return []
        if error.code == 429 or "RESOURCE_EXHAUSTED" in str(error.status):
            return [
                StreamEvent(
                    event_type=StreamEventType.RATE_LIMIT,
                    retry_delay_ms=int_or_none(event.retry_after_ms or error.retry_after_ms),
                    error_category="rate_limit",
                    raw=data,
                )
            ]
        return []

    def _parse_tool_calls(self, raw_calls: object, data: dict[str, object]) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        calls: object = [raw_calls] if isinstance(raw_calls, dict) else raw_calls
        if isinstance(calls, list):
            for raw_call in calls:
                call = validate_or_none(_GeminiToolCall, raw_call)
                if call is None:
                    continue
                tool_name = str_or_none(call.name)
                tool_args = dict_or_none(call.args)
                self._tool_seq += 1
                tool_id = f"gemini-tool-{self._tool_seq}"
                self._open_tools.append(tool_id)
                events.append(
                    StreamEvent(
                        event_type=StreamEventType.TOOL_START,
                        tool_name=tool_name,
                        tool_id=tool_id,
                        tool_detail=extract_tool_detail(tool_name, tool_args),
                        tool_status="running",
                        raw=data,
                    )
                )
                # Emit SUBAGENT when an agent-like tool is invoked.
                if tool_name and "agent" in tool_name.lower():
                    desc = str(tool_args.get("description", "")) if tool_args else ""
                    events.append(
                        StreamEvent(
                            event_type=StreamEventType.SUBAGENT,
                            text_preview=f"Subagent: {desc}" if desc else "Subagent",
                            raw=data,
                        )
                    )
        return events

    def _parse_tool_response(
        self, event: _GeminiEvent, data: dict[str, object]
    ) -> list[StreamEvent]:
        response = validate_or_none(_GeminiResponse, event.functionResponse or event.tool_response)
        tool_name = str_or_none(response.name) if response is not None else None
        if self._open_tools:
            tool_id = self._open_tools.popleft()
        else:
            self._orphan_seq += 1
            tool_id = f"gemini-tool-orphan-{self._orphan_seq}"
        return [
            StreamEvent(
                event_type=StreamEventType.TOOL_DONE,
                tool_name=tool_name,
                tool_id=tool_id,
                tool_status="done",
                raw=data,
            ),
            # Transition back to writing state after the tool completes.
            StreamEvent(
                event_type=StreamEventType.STATE_CHANGE,
                text_preview="writing",
                raw=data,
            ),
        ]

    def _parse_result_blob(self, event: _GeminiEvent, data: dict[str, object]) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        text_val = ""
        if isinstance(event.candidates, list) and event.candidates:
            candidate = validate_or_none(_GeminiCandidate, event.candidates[0])
            if candidate is not None:
                content = validate_or_none(_GeminiContent, candidate.content)
                if content is not None and isinstance(content.parts, list) and content.parts:
                    part = validate_or_none(_GeminiPart, content.parts[0])
                    if part is not None:
                        text_val = str(part.text)
                # Emit STATE_CHANGE when the finish reason signals a transition.
                if str(candidate.finishReason) == "STOP":
                    events.append(
                        StreamEvent(
                            event_type=StreamEventType.STATE_CHANGE,
                            text_preview="writing",
                            raw=data,
                        )
                    )
        if text_val:
            events.append(
                StreamEvent(
                    event_type=StreamEventType.TEXT,
                    text_preview=text_val[:200],
                    text_full=text_val,
                    raw=data,
                )
            )
        if "usageMetadata" in event.model_fields_set:
            events.append(StreamEvent(event_type=StreamEventType.RESULT, raw=data))
        return events
