"""Antigravity CLI (``agy -p ... --output-format stream-json``) wire format.

Every line carries an ``event`` discriminator and a payload keyed by the same
name::

    {"event": "init", "conversation_id": "...", "init": {"model": "...", ...}}
    {"event": "step_update", "step_update": {"step_index": 2, "state": "ACTIVE",
     "step_type": "tool", "tool_name": "view_file", "tool_info": {...}}}
    {"event": "result", "result": {"status": "SUCCESS", "response": "...", ...}}

A conversation is a sequence of numbered steps (``user_input``,
``agent_response``, ``tool``, ``checkpoint``, ``error_message``). Each step
reports ``ACTIVE`` while it runs and finishes as ``DONE`` or ``ERROR``.
Response text streams as ``text_delta`` fragments on ``agent_response``
steps; their concatenation is the response. Thinking is not streamed (only
``usage.thinking_tokens``), and there is no cost field.

Tool correlation: tool ids are derived from ``conversation_id`` and
``step_index``, which are unique per step, so overlapping tools pair exactly.
The parser remembers which steps it has started so repeated ``ACTIVE``
updates become TOOL_EXEC progress and a step that finishes without a prior
``ACTIVE`` update still produces a TOOL_START/TOOL_DONE pair.
"""

from __future__ import annotations

import logging
import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from orchcore.stream.events import StreamEvent, StreamEventType
from orchcore.stream.formats._shared import (
    _WireValidationError,
    dict_or_none,
    error_text_or_none,
    extract_tool_detail,
    first_str,
    int_or_none,
    str_or_none,
    to_decimal,
    token_usage_or_none,
    validate_or_none,
)

logger: logging.Logger = logging.getLogger(__name__)

# Quota and rate-limit wording in Antigravity error text (Gemini API status
# names, HTTP 429, and the CLI's own quota messages).
_RATE_LIMIT_RE = re.compile(
    r"RESOURCE_EXHAUSTED|rate[_\s-]?limit|quota|too many requests|\b429\b",
    re.IGNORECASE,
)

# Tools that run another agent; their start also emits SUBAGENT.
_SUBAGENT_TOOLS = frozenset({"invoke_subagent", "browser_subagent"})

# Parameter names, in priority order, that make a useful one-line tool detail.
_DETAIL_PARAMETERS = (
    "AbsolutePath",
    "TargetFile",
    "DirectoryPath",
    "SearchPath",
    "Url",
    "Query",
    "Pattern",
)


class _AntigravityToolInfo(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    name: object = None
    parameters: object = None
    error: object = None


class _AntigravityStep(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    conversation_id: object = None
    step_index: object = None
    state: object = ""
    step_type: object = ""
    tool_name: object = None
    tool_info: object = None
    text_delta: object = None
    duration_seconds: object = None
    error: object = None
    message: object = None
    subagent_info: object = None


class _AntigravitySubagent(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    type_name: object = None
    role: object = None


class _AntigravitySubagentInfo(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    subagents: object = None


class _AntigravityResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    conversation_id: object = None
    status: object = ""
    error: object = None
    duration_seconds: object = None
    num_turns: object = None
    usage: object = None
    denied_actions: object = None


class _AntigravityEvent(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    event: str = ""
    conversation_id: object = None
    step_update: object = None
    result: object = None


class AntigravityParser:
    """Stateful parser for Antigravity stream-json objects (open tool steps)."""

    def __init__(self) -> None:
        # Tool ids that have emitted TOOL_START and not yet TOOL_DONE.
        self._open_tools: set[str] = set()

    def parse(self, data: dict[str, object]) -> list[StreamEvent]:
        """Parse one Antigravity wire object into zero or more events."""
        try:
            event = _AntigravityEvent.model_validate(data)
        except ValidationError as exc:
            raise _WireValidationError(str(exc)) from exc

        match event.event:
            case "init":
                return [
                    StreamEvent(
                        event_type=StreamEventType.INIT,
                        session_id=str_or_none(event.conversation_id),
                        raw=data,
                    )
                ]
            case "step_update":
                step = validate_or_none(_AntigravityStep, event.step_update)
                if step is None:
                    return []
                return self._parse_step(step, data)
            case "result":
                result = validate_or_none(_AntigravityResult, event.result)
                if result is None:
                    return []
                return _parse_result(result, data)
            case _:
                logger.debug("Antigravity: unknown event %r", event.event)
                return []

    def _parse_step(self, step: _AntigravityStep, data: dict[str, object]) -> list[StreamEvent]:
        match str(step.step_type):
            case "tool":
                return self._parse_tool_step(step, data)
            case "agent_response":
                return _parse_agent_response(step, data)
            case "error_message":
                return _step_error_events(_step_error_text(step), data)
            case "user_input" | "checkpoint":
                return []
            case _:
                if str(step.state) == "ERROR":
                    return _step_error_events(_step_error_text(step), data)
                logger.debug("Antigravity: unknown step type %r", step.step_type)
                return []

    def _parse_tool_step(
        self, step: _AntigravityStep, data: dict[str, object]
    ) -> list[StreamEvent]:
        info = validate_or_none(_AntigravityToolInfo, step.tool_info) or _AntigravityToolInfo()
        tool_name = first_str(step.tool_name, info.name)
        parameters = dict_or_none(info.parameters)
        tool_detail = _tool_detail(tool_name, parameters)
        tool_id = _tool_id(step)
        state = str(step.state)

        events: list[StreamEvent] = []
        if tool_id is None or tool_id not in self._open_tools:
            if tool_id is not None:
                self._open_tools.add(tool_id)
            events.append(
                StreamEvent(
                    event_type=StreamEventType.TOOL_START,
                    tool_id=tool_id,
                    tool_name=tool_name,
                    tool_detail=tool_detail,
                    tool_status="running",
                    raw=data,
                )
            )
            if tool_name in _SUBAGENT_TOOLS:
                description = _subagent_description(step) or tool_detail
                events.append(
                    StreamEvent(
                        event_type=StreamEventType.SUBAGENT,
                        text_preview=(
                            f"Subagent: {description}"[:200] if description else "Subagent"
                        ),
                        raw=data,
                    )
                )
        elif state == "ACTIVE":
            # A repeated ACTIVE update for a running step is progress.
            events.append(
                StreamEvent(
                    event_type=StreamEventType.TOOL_EXEC,
                    tool_id=tool_id,
                    tool_name=tool_name,
                    tool_detail=tool_detail,
                    tool_status="running",
                    raw=data,
                )
            )

        if state in ("DONE", "ERROR"):
            if tool_id is not None:
                self._open_tools.discard(tool_id)
            failed = state == "ERROR"
            events.append(
                StreamEvent(
                    event_type=StreamEventType.TOOL_DONE,
                    tool_id=tool_id,
                    tool_name=tool_name,
                    tool_detail=tool_detail,
                    tool_status="error" if failed else "done",
                    tool_duration_ms=_seconds_to_ms(step.duration_seconds),
                    error=error_text_or_none(info.error) if failed else None,
                    raw=data,
                )
            )
        return events


def _parse_agent_response(step: _AntigravityStep, data: dict[str, object]) -> list[StreamEvent]:
    if str(step.state) == "ERROR":
        return _step_error_events(_step_error_text(step), data)
    text = step.text_delta if isinstance(step.text_delta, str) else ""
    if not text:
        return []
    return [
        StreamEvent(
            event_type=StreamEventType.TEXT,
            text_preview=text[:200],
            text_full=text,
            raw=data,
        )
    ]


def _step_error_text(step: _AntigravityStep) -> str | None:
    """Return the error text a failed or ``error_message`` step carries."""
    return error_text_or_none(step.error) or first_str(step.message, step.text_delta)


def _subagent_description(step: _AntigravityStep) -> str | None:
    """Describe the first subagent in ``subagent_info``, when the step has one."""
    info = validate_or_none(_AntigravitySubagentInfo, step.subagent_info)
    if info is None or not isinstance(info.subagents, list) or not info.subagents:
        return None
    subagent = validate_or_none(_AntigravitySubagent, info.subagents[0])
    if subagent is None:
        return None
    return first_str(subagent.role, subagent.type_name)


def _step_error_events(error: str | None, data: dict[str, object]) -> list[StreamEvent]:
    """Classify a failed non-tool step as a rate limit or a generic error."""
    if error is not None and _RATE_LIMIT_RE.search(error):
        return [
            StreamEvent(
                event_type=StreamEventType.RATE_LIMIT,
                error=error,
                error_category="rate_limit",
                raw=data,
            )
        ]
    return [
        StreamEvent(
            event_type=StreamEventType.ERROR,
            error=error or "antigravity step failed",
            error_category="error",
            raw=data,
        )
    ]


def _parse_result(result: _AntigravityResult, data: dict[str, object]) -> list[StreamEvent]:
    status = str(result.status)
    error: str | None = None
    if status and status != "SUCCESS":
        error = error_text_or_none(result.error) or f"antigravity reported status {status}"
    if isinstance(result.denied_actions, list) and result.denied_actions:
        # Headless runs auto-deny tools that need approval and still report
        # SUCCESS; the denial is visible only here and on stderr.
        logger.info(
            "Antigravity denied %d tool action(s) that need approval in headless mode",
            len(result.denied_actions),
        )
    events = [
        StreamEvent(
            event_type=StreamEventType.RESULT,
            duration_ms=_seconds_to_ms(result.duration_seconds),
            num_turns=int_or_none(result.num_turns),
            session_id=str_or_none(result.conversation_id),
            token_usage=token_usage_or_none(result.usage),
            error=error,
            raw=data,
        )
    ]
    # A quota failure ends the run with status ERROR; surface it as a typed
    # rate limit after the RESULT so the runner classifies the run correctly.
    if error is not None and _RATE_LIMIT_RE.search(error):
        events.append(
            StreamEvent(
                event_type=StreamEventType.RATE_LIMIT,
                error=error,
                error_category="rate_limit",
                raw=data,
            )
        )
    return events


def _tool_id(step: _AntigravityStep) -> str | None:
    """Derive a per-step tool id; ``None`` when the step carries no index."""
    step_index = int_or_none(step.step_index)
    if step_index is None:
        return None
    conversation_id = str_or_none(step.conversation_id) or "step"
    return f"{conversation_id}:{step_index}"


def _tool_detail(tool_name: str | None, parameters: dict[str, object] | None) -> str | None:
    """Derive a short detail string from Antigravity's PascalCase tool parameters."""
    if not tool_name or not parameters:
        return None
    if tool_name == "run_command":
        command = str_or_none(parameters.get("CommandLine"))
        return f"$ {command}"[:80] if command else None
    detail = first_str(*(parameters.get(key) for key in _DETAIL_PARAMETERS))
    if detail is not None:
        return detail[:80]
    return extract_tool_detail(tool_name, parameters)


def _seconds_to_ms(value: object) -> int | None:
    """Convert a wire ``duration_seconds`` value into whole milliseconds."""
    seconds = to_decimal(value)
    if seconds is None or not seconds.is_finite():
        return None
    return round(seconds * 1000)
