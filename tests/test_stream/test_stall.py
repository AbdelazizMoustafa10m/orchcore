from __future__ import annotations

from itertools import chain, repeat

import pytest

from orchcore.stream.events import StreamEvent, StreamEventType
from orchcore.stream.stall import StallDetector


class _AsyncEventIterator:
    def __init__(self, events: list[StreamEvent]) -> None:
        self._events = iter(events)

    def __aiter__(self):
        return self

    async def __anext__(self) -> StreamEvent:
        try:
            return next(self._events)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def _patch_watch_timing(
    monkeypatch: pytest.MonkeyPatch,
    *,
    steps: list[str],
    monotonic_values: list[float],
) -> None:
    step_iter = iter(steps)
    time_iter = chain(monotonic_values, repeat(monotonic_values[-1]))

    async def fake_wait_for(coro, timeout):
        del timeout
        step = next(step_iter)
        if step == "timeout":
            coro.close()
            raise TimeoutError
        return await coro

    monkeypatch.setattr("orchcore.stream.stall.asyncio.wait_for", fake_wait_for)
    monkeypatch.setattr("orchcore.stream.stall.time.monotonic", lambda: next(time_iter))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("steps", "monotonic_values", "source_events", "expected_types"),
    [
        pytest.param(
            ["event", "timeout", "event", "event"],
            [0.0, 0.0, 0.02, 0.02],
            [
                StreamEvent(event_type=StreamEventType.INIT),
                StreamEvent(event_type=StreamEventType.TEXT, text_preview="ready"),
            ],
            [StreamEventType.INIT, StreamEventType.STALL, StreamEventType.TEXT],
            id="single-stall-during-idle",
        ),
        pytest.param(
            ["event", "timeout", "event", "timeout", "event", "event"],
            [0.0, 0.0, 0.02, 0.02, 0.04, 0.04],
            [
                StreamEvent(event_type=StreamEventType.INIT),
                StreamEvent(event_type=StreamEventType.TEXT, text_preview="part 1"),
                StreamEvent(event_type=StreamEventType.RESULT),
            ],
            [
                StreamEventType.INIT,
                StreamEventType.STALL,
                StreamEventType.TEXT,
                StreamEventType.STALL,
                StreamEventType.RESULT,
            ],
            id="resets-and-stalls-again",
        ),
        pytest.param(
            ["event", "timeout", "event", "timeout", "event", "event"],
            [0.0, 0.0, 0.02, 0.02, 0.05, 0.05],
            [
                StreamEvent(
                    event_type=StreamEventType.TOOL_START,
                    tool_name="mcp__exa__web_search_exa",
                    tool_id="tool-1",
                ),
                StreamEvent(
                    event_type=StreamEventType.HEARTBEAT,
                    text_preview="Gemini processing (line 10)",
                ),
                StreamEvent(
                    event_type=StreamEventType.TOOL_DONE,
                    tool_name="mcp__exa__web_search_exa",
                    tool_id="tool-1",
                    tool_status="done",
                ),
            ],
            [
                StreamEventType.TOOL_START,
                StreamEventType.HEARTBEAT,
                StreamEventType.TOOL_DONE,
            ],
            id="heartbeat-resets-idle-without-clearing-active-tool",
        ),
    ],
)
async def test_watch_stall_scenarios(
    monkeypatch: pytest.MonkeyPatch,
    steps: list[str],
    monotonic_values: list[float],
    source_events: list[StreamEvent],
    expected_types: list[StreamEventType],
) -> None:
    # Arrange
    _patch_watch_timing(monkeypatch, steps=steps, monotonic_values=monotonic_values)
    detector = StallDetector(normal_timeout=0.01, deep_timeout=0.04, check_interval=0.002)
    source = _AsyncEventIterator(source_events)

    # Act
    events = [event async for event in detector.watch(source)]

    # Assert
    assert [event.event_type for event in events] == expected_types


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    [
        pytest.param("mcp__exa__web_search_exa", id="exa-mcp-name"),
        pytest.param("mcp__tavily__tavily_search", id="tavily-mcp-name"),
    ],
)
async def test_watch_uses_deep_timeout_for_full_mcp_tool_names(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    # Arrange
    _patch_watch_timing(
        monkeypatch,
        steps=["event", "timeout", "event", "event"],
        monotonic_values=[0.0, 0.0, 0.02, 0.02],
    )
    detector = StallDetector(normal_timeout=0.005, deep_timeout=0.04, check_interval=0.002)
    source = _AsyncEventIterator(
        [
            StreamEvent(
                event_type=StreamEventType.TOOL_START,
                tool_name=tool_name,
                tool_id="tool-1",
            ),
            StreamEvent(
                event_type=StreamEventType.TOOL_DONE,
                tool_name=tool_name,
                tool_id="tool-1",
                tool_status="done",
            ),
        ]
    )

    # Act
    events = [event async for event in detector.watch(source)]

    # Assert
    assert [event.event_type for event in events] == [
        StreamEventType.TOOL_START,
        StreamEventType.TOOL_DONE,
    ]
