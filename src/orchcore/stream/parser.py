"""Public stream-parsing API: dispatch plus shared diagnostics (WP-25).

Format knowledge lives in :mod:`orchcore.stream.formats` (one module per
format, typed Pydantic envelope models). This module owns what is shared:
JSONL line handling, the malformed-JSON counter, and the wire-validation
counter. The public surface (``StreamParser``, ``parse_line``,
``parse_stream``, ``json_parse_error_count``) is unchanged by the refactor.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from orchcore.stream.events import StreamEvent, StreamFormat  # noqa: TC001 — runtime use
from orchcore.stream.formats import (
    PARSER_FACTORIES,
    _WireValidationError,
    extract_tool_detail,
    to_decimal,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from orchcore.stream.formats import FormatParser

logger: logging.Logger = logging.getLogger(__name__)
_MAX_JSON_PARSE_WARNINGS = 3
_MAX_WIRE_VALIDATION_WARNINGS = 3


class StreamParser:
    """Parses JSONL streams from a given agent format into typed StreamEvent objects."""

    # Shared helpers re-exposed for direct use and tests; the canonical
    # implementations live in orchcore.stream.formats._shared.
    _extract_tool_detail = staticmethod(extract_tool_detail)
    _to_decimal = staticmethod(to_decimal)

    def __init__(self, stream_format: StreamFormat) -> None:
        self._format = stream_format
        self._json_parse_error_count = 0
        self._wire_validation_error_count = 0
        self._format_parser: FormatParser = PARSER_FACTORIES[stream_format]()

    def parse_line(self, line: str) -> list[StreamEvent]:
        """Parse one JSONL line. Returns zero or more StreamEvents.

        On malformed JSON: increments ``json_parse_error_count``, emits bounded
        warnings, and returns an empty list.
        On well-formed JSON that fails the format's wire schema: increments
        ``wire_validation_error_count``, emits bounded warnings, and returns an
        empty list (schema failures are counted, never silently dropped).
        On unknown event type: logs debug, returns empty list.
        """
        stripped = line.strip()
        if not stripped:
            return []
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            self._json_parse_error_count += 1
            if self._json_parse_error_count <= _MAX_JSON_PARSE_WARNINGS:
                logger.warning(
                    "Malformed JSON line for %s stream (count=%d): %s",
                    self._format.value,
                    self._json_parse_error_count,
                    stripped[:200],
                )
            elif self._json_parse_error_count == _MAX_JSON_PARSE_WARNINGS + 1:
                logger.warning(
                    "Suppressing further malformed JSON warnings for %s stream after %d "
                    "parse errors",
                    self._format.value,
                    _MAX_JSON_PARSE_WARNINGS,
                )
            return []
        if not isinstance(data, dict):
            return []
        try:
            return self._format_parser.parse(data)
        except _WireValidationError:
            self._wire_validation_error_count += 1
            if self._wire_validation_error_count <= _MAX_WIRE_VALIDATION_WARNINGS:
                logger.warning(
                    "Wire-schema validation failed for %s stream (count=%d): %s",
                    self._format.value,
                    self._wire_validation_error_count,
                    stripped[:200],
                )
            elif self._wire_validation_error_count == _MAX_WIRE_VALIDATION_WARNINGS + 1:
                logger.warning(
                    "Suppressing further wire-schema validation warnings for %s stream "
                    "after %d errors",
                    self._format.value,
                    _MAX_WIRE_VALIDATION_WARNINGS,
                )
            return []

    @property
    def json_parse_error_count(self) -> int:
        """Return the number of malformed JSON lines seen by this parser instance."""
        return self._json_parse_error_count

    @property
    def wire_validation_error_count(self) -> int:
        """Return the number of well-formed lines that failed the wire schema."""
        return self._wire_validation_error_count

    async def parse_stream(self, stream: AsyncIterator[str]) -> AsyncGenerator[StreamEvent, None]:
        """Async generator yielding StreamEvents from an async line iterator."""
        async for line in stream:
            for event in self.parse_line(line):
                yield event
