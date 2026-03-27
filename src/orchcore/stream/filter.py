from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from orchcore.stream.events import StreamFormat

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


type _SkipMatcher = tuple[str, ...]


def _type_matchers(*event_types: str) -> tuple[_SkipMatcher, ...]:
    return tuple(
        matcher
        for event_type in event_types
        for matcher in (
            (f'"type":"{event_type}"',),
            (f'"type": "{event_type}"',),
        )
    )


class StreamFilter:
    """
    Drops high-volume events before parsing.

    Uses string-level matching BEFORE json.loads() to avoid parsing ~95% of lines
    that are dropped. This is the key optimization.
    """

    SKIP_MATCHERS: ClassVar[dict[StreamFormat, tuple[_SkipMatcher, ...]]] = {
        StreamFormat.CLAUDE: _type_matchers(
            "content_block_stop",
            "message_start",
            "message_stop",
        ),
        StreamFormat.CODEX: _type_matchers(
            "response.output_text.delta",
            "response.reasoning_summary.delta",
            "turn.started",
        ),
        StreamFormat.OPENCODE: (
            ('"type":"text"', '"text":""'),
            ('"type": "text"', '"text": ""'),
        ),
        StreamFormat.GEMINI: (('"promptFeedback"',),),
        StreamFormat.COPILOT: (
            ('"text":""',),
            ('"text": ""',),
            ('"message":""',),
            ('"message": ""',),
            ('"content":""',),
            ('"content": ""',),
        ),
    }

    def __init__(self, stream_format: StreamFormat) -> None:
        self._format = stream_format
        self._skip_matchers = self.SKIP_MATCHERS.get(stream_format, ())

    def should_keep(self, line: str) -> bool:
        """
        Fast-path: use string matching BEFORE json.loads() to drop high-volume events.
        This avoids parsing ~95% of lines that would be discarded anyway.

        Supports all five formats: Claude, Codex, OpenCode, Gemini, and Copilot.
        Each format has its own set of skip matchers defined in SKIP_MATCHERS:
          - Claude: content_block_stop, message_start, message_stop
          - Codex: response.output_text.delta, response.reasoning_summary.delta, turn.started
          - OpenCode: empty "text" events
          - Gemini: promptFeedback lines
          - Copilot: empty text/message/content fields

        Empty lines and whitespace-only lines are always dropped.
        If a line does not match any skip matcher it is kept, even if malformed.
        Only drop lines that positively match a skip pattern.
        """
        if not line or not line.strip():
            return False

        return not any(
            all(fragment in line for fragment in matcher) for matcher in self._skip_matchers
        )

    async def filter_stream(self, raw: AsyncIterator[str]) -> AsyncIterator[str]:
        """Async generator yielding only actionable JSONL lines."""
        async for line in raw:
            if self.should_keep(line):
                yield line
