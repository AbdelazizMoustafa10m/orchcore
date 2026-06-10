"""Per-format wire models for agent JSONL streams (WP-25, analysis/06 plan).

One module per supported :class:`~orchcore.stream.events.StreamFormat`, each
with private Pydantic envelope models (``extra="allow"`` for forward
compatibility) and a parser class exposing
``parse(data: dict[str, object]) -> list[StreamEvent]``.

:class:`~orchcore.stream.parser.StreamParser` keeps the public API and owns
the shared infrastructure (malformed-JSON counting, wire-validation counting,
line splitting); these modules own all format knowledge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchcore.stream.events import StreamFormat
from orchcore.stream.formats._shared import (
    FormatParser,
    _WireValidationError,
    extract_tool_detail,
    to_decimal,
)
from orchcore.stream.formats.claude import ClaudeParser
from orchcore.stream.formats.codex import CodexParser
from orchcore.stream.formats.copilot import CopilotParser
from orchcore.stream.formats.gemini import GeminiParser
from orchcore.stream.formats.opencode import OpenCodeParser

if TYPE_CHECKING:
    from collections.abc import Callable

PARSER_FACTORIES: dict[StreamFormat, Callable[[], FormatParser]] = {
    StreamFormat.CLAUDE: ClaudeParser,
    StreamFormat.CODEX: CodexParser,
    StreamFormat.COPILOT: CopilotParser,
    StreamFormat.OPENCODE: OpenCodeParser,
    StreamFormat.GEMINI: GeminiParser,
}

__all__ = [
    "PARSER_FACTORIES",
    "ClaudeParser",
    "CodexParser",
    "CopilotParser",
    "FormatParser",
    "GeminiParser",
    "OpenCodeParser",
    "_WireValidationError",
    "extract_tool_detail",
    "to_decimal",
]
