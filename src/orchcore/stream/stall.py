from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, ClassVar, Final

from orchcore.stream.events import StreamEvent, StreamEventType

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _StreamExhausted:
    """Sentinel returned when the wrapped async iterator is exhausted."""


_STREAM_EXHAUSTED: Final = _StreamExhausted()


async def _next_or_sentinel(
    aiter: AsyncIterator[StreamEvent],
) -> StreamEvent | _StreamExhausted:
    """Await the next item from an async iterator, returning a sentinel on exhaustion."""
    try:
        return await aiter.__anext__()
    except StopAsyncIteration:
        return _STREAM_EXHAUSTED


class StallDetector:
    """Wraps an event stream, injecting STALL events when the agent goes silent."""

    # Substrings matched case-insensitively against active tool names.
    # MCP tool names like ``mcp__exa__deep_search_exa`` match ``deep_search``
    # because the check is substring-based rather than exact equality.
    DEEP_TOOL_PATTERNS: ClassVar[frozenset[str]] = frozenset(
        {
            "deep_search",
            "deep_researcher",
            "deep_research",
            "web_search_exa",
            "tavily_research",
            "tavily_crawl",
            "tavily_search",
            "mcp__exa__web_search_exa",
            "mcp__exa__deep_search_exa",
            "mcp__exa__deep_researcher_start",
            "mcp__exa__deep_researcher_check",
            "mcp__tavily__tavily_search",
            "mcp__tavily__tavily_research",
            "mcp__tavily__tavily_crawl",
        }
    )

    def __init__(
        self,
        normal_timeout: float = 300.0,
        deep_timeout: float = 600.0,
        check_interval: float = 5.0,
    ) -> None:
        self._normal_timeout = normal_timeout
        self._deep_timeout = deep_timeout
        self._check_interval = check_interval
        self._active_tools: set[str] = set()

    def _is_deep_tool_active(self) -> bool:
        """Return True if any active tool name contains a deep-tool pattern (case-insensitive)."""
        return any(
            pattern in tool_name.lower()
            for tool_name in self._active_tools
            for pattern in self.DEEP_TOOL_PATTERNS
        )

    def _current_timeout(self) -> float:
        """Return the applicable stall timeout based on current active tools."""
        return self._deep_timeout if self._is_deep_tool_active() else self._normal_timeout

    def _track_tool(self, event: StreamEvent) -> None:
        """Update active tool set from TOOL_START / TOOL_DONE events."""
        if event.event_type == StreamEventType.HEARTBEAT:
            return
        if event.event_type == StreamEventType.TOOL_START and event.tool_name is not None:
            self._active_tools.add(event.tool_name)
        elif event.event_type == StreamEventType.TOOL_DONE and event.tool_name is not None:
            self._active_tools.discard(event.tool_name)

    async def watch(self, events: AsyncIterator[StreamEvent]) -> AsyncIterator[StreamEvent]:
        """Yield events from the wrapped stream, injecting STALL events on silence.

        This shipped API differs from the earlier design doc: callers pass the
        upstream async event iterator into ``watch()`` and iterate over the
        returned stream. There is no separate ``activity()`` method or background
        watcher task that pushes directly into a ``UICallback``. Instead,
        ``watch()`` forwards real events, updates tool activity internally, and
        yields synthetic ``STALL`` events inline when silence crosses the active
        timeout.

        Uses ``asyncio.wait_for()`` with ``check_interval`` as the polling
        window. When no event arrives within that window, idle time is measured
        against ``_current_timeout()``. A synthetic ``STALL`` event is yielded
        once per stall period; the flag resets when a real event arrives.
        """
        aiter_obj = events.__aiter__()
        last_event_at = time.monotonic()
        stall_injected = False

        while True:
            try:
                result = await asyncio.wait_for(
                    _next_or_sentinel(aiter_obj),
                    timeout=self._check_interval,
                )
            except TimeoutError:
                idle = time.monotonic() - last_event_at
                if idle >= self._current_timeout() and not stall_injected:
                    stall_injected = True
                    yield StreamEvent(event_type=StreamEventType.STALL, idle_seconds=idle)
                continue

            if isinstance(result, _StreamExhausted):
                break

            last_event_at = time.monotonic()
            stall_injected = False
            self._track_tool(result)
            yield result
