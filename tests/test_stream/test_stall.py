from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import pytest

from orchcore.stream.events import StreamEvent, StreamEventType
from orchcore.stream.stall import StallDetector

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def _paced(*steps: tuple[float, StreamEvent]) -> AsyncIterator[StreamEvent]:
    """Sleep for each step's delay, then yield its event.

    This is an async generator on purpose, like the runner's stream chain:
    cancelling a pending ``__anext__()`` closes it for good, which is what the
    stall detector's polling must never do.
    """
    for delay, event in steps:
        await asyncio.sleep(delay)
        yield event


class _BlockingUpstream:
    """Yields one INIT event, then blocks until cancelled, recording the cancellation."""

    def __init__(self) -> None:
        self.blocked = asyncio.Event()
        self.cancelled = False

    async def stream(self) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(event_type=StreamEventType.INIT)
        self.blocked.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.fixture
def blocking_upstream() -> _BlockingUpstream:
    return _BlockingUpstream()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("steps", "expected_types"),
    [
        pytest.param(
            [
                (0.0, StreamEvent(event_type=StreamEventType.INIT)),
                (0.25, StreamEvent(event_type=StreamEventType.TEXT, text_preview="ready")),
            ],
            [StreamEventType.INIT, StreamEventType.STALL, StreamEventType.TEXT],
            id="single-stall-during-idle",
        ),
        pytest.param(
            [
                (0.0, StreamEvent(event_type=StreamEventType.INIT)),
                (0.25, StreamEvent(event_type=StreamEventType.TEXT, text_preview="part 1")),
                (0.25, StreamEvent(event_type=StreamEventType.RESULT)),
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
            [
                (
                    0.0,
                    StreamEvent(
                        event_type=StreamEventType.TOOL_START,
                        tool_name="mcp__exa__web_search_exa",
                        tool_id="tool-1",
                    ),
                ),
                (
                    0.2,
                    StreamEvent(
                        event_type=StreamEventType.HEARTBEAT,
                        text_preview="Agent processing (line 10)",
                    ),
                ),
                (
                    0.2,
                    StreamEvent(
                        event_type=StreamEventType.TOOL_DONE,
                        tool_name="mcp__exa__web_search_exa",
                        tool_id="tool-1",
                        tool_status="done",
                    ),
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
    steps: list[tuple[float, StreamEvent]],
    expected_types: list[StreamEventType],
) -> None:
    # Arrange
    detector = StallDetector(normal_timeout=0.08, deep_timeout=0.3, check_interval=0.02)

    # Act
    events = [event async for event in detector.watch(_paced(*steps))]

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
async def test_watch_uses_deep_timeout_for_full_mcp_tool_names(tool_name: str) -> None:
    # Arrange
    detector = StallDetector(normal_timeout=0.08, deep_timeout=0.3, check_interval=0.02)
    source = _paced(
        (
            0.0,
            StreamEvent(event_type=StreamEventType.TOOL_START, tool_name=tool_name, tool_id="t-1"),
        ),
        (
            0.2,
            StreamEvent(
                event_type=StreamEventType.TOOL_DONE,
                tool_name=tool_name,
                tool_id="t-1",
                tool_status="done",
            ),
        ),
    )

    # Act
    events = [event async for event in detector.watch(source)]

    # Assert
    assert [event.event_type for event in events] == [
        StreamEventType.TOOL_START,
        StreamEventType.TOOL_DONE,
    ]


@pytest.mark.asyncio
async def test_watch_keeps_reading_after_pauses_longer_than_check_interval() -> None:
    # Arrange: the first pause spans several polls but stays under the stall
    # timeout; the second exceeds it.
    detector = StallDetector(normal_timeout=0.2, deep_timeout=0.2, check_interval=0.03)
    source = _paced(
        (0.0, StreamEvent(event_type=StreamEventType.TEXT, text_preview="a")),
        (0.1, StreamEvent(event_type=StreamEventType.TEXT, text_preview="b")),
        (0.4, StreamEvent(event_type=StreamEventType.TEXT, text_preview="c")),
    )

    # Act
    events = [event async for event in detector.watch(source)]

    # Assert: every event arrives in order, with one STALL for the long pause only.
    assert [(event.event_type, event.text_preview) for event in events] == [
        (StreamEventType.TEXT, "a"),
        (StreamEventType.TEXT, "b"),
        (StreamEventType.STALL, None),
        (StreamEventType.TEXT, "c"),
    ]
    stall = events[2]
    assert stall.idle_seconds is not None
    assert stall.idle_seconds >= 0.2


@pytest.mark.asyncio
async def test_watch_propagates_upstream_error_after_polling_timeouts() -> None:
    # Arrange
    tasks_before = asyncio.all_tasks()

    async def failing_upstream() -> AsyncIterator[StreamEvent]:
        yield StreamEvent(event_type=StreamEventType.INIT)
        await asyncio.sleep(0.1)
        raise RuntimeError("stream broke")

    detector = StallDetector(normal_timeout=10, deep_timeout=10, check_interval=0.02)
    seen: list[StreamEventType] = []

    # Act
    with pytest.raises(RuntimeError, match="stream broke"):
        async for event in detector.watch(failing_upstream()):
            seen.append(event.event_type)

    # Assert
    assert seen == [StreamEventType.INIT]
    assert asyncio.all_tasks() == tasks_before


@pytest.mark.asyncio
async def test_watch_aclose_cancels_pending_upstream_read(
    blocking_upstream: _BlockingUpstream,
) -> None:
    # Arrange
    tasks_before = asyncio.all_tasks()
    detector = StallDetector(normal_timeout=0.05, deep_timeout=0.05, check_interval=0.02)
    watched = detector.watch(blocking_upstream.stream())
    assert isinstance(watched, AsyncGenerator)

    # Act: the STALL proves an upstream read is still pending when the consumer stops.
    first = await anext(watched)
    stall = await anext(watched)
    await watched.aclose()

    # Assert
    assert first.event_type is StreamEventType.INIT
    assert stall.event_type is StreamEventType.STALL
    assert blocking_upstream.cancelled
    assert asyncio.all_tasks() == tasks_before


@pytest.mark.asyncio
async def test_watch_cancellation_cancels_pending_upstream_read(
    blocking_upstream: _BlockingUpstream,
) -> None:
    # Arrange
    tasks_before = asyncio.all_tasks()
    detector = StallDetector(normal_timeout=10, deep_timeout=10, check_interval=0.02)

    async def consume() -> None:
        async for _event in detector.watch(blocking_upstream.stream()):
            pass

    consumer = asyncio.create_task(consume())
    await blocking_upstream.blocked.wait()

    # Act
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    # Assert
    assert blocking_upstream.cancelled
    assert asyncio.all_tasks() == tasks_before
