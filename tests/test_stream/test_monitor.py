from __future__ import annotations

from decimal import Decimal

import pytest

from orchcore.stream.events import AgentState, StreamEvent, StreamEventType
from orchcore.stream.monitor import AgentMonitor, _friendly_name


def test_friendly_name_maps_known_tools() -> None:
    assert _friendly_name("Read") == "Read file"
    assert _friendly_name("CustomTool") == "CustomTool"


def test_monitor_tracks_state_counters_and_results() -> None:
    # Arrange
    monitor = AgentMonitor("claude")

    monitor.update(StreamEvent(event_type=StreamEventType.INIT, session_id="sess-1"))
    monitor.update(
        StreamEvent(
            event_type=StreamEventType.STATE_CHANGE,
            text_preview="thinking",
        )
    )
    thinking_snapshot = monitor.snapshot()
    monitor.update(
        StreamEvent(
            event_type=StreamEventType.TOOL_START,
            tool_name="Read",
            tool_id="tool-1",
            tool_detail="README.md",
            tool_status="running",
        )
    )
    monitor.update(
        StreamEvent(
            event_type=StreamEventType.TOOL_EXEC,
            tool_id="tool-1",
            tool_detail="src/planora/core/config.py",
        )
    )
    monitor.update(StreamEvent(event_type=StreamEventType.TEXT, text_preview="draft"))
    monitor.update(
        StreamEvent(
            event_type=StreamEventType.TOOL_DONE,
            tool_name="Read",
            tool_id="tool-1",
            tool_detail="src/planora/core/config.py",
            tool_status="done",
        )
    )
    monitor.update(StreamEvent(event_type=StreamEventType.SUBAGENT))
    monitor.update(
        StreamEvent(
            event_type=StreamEventType.RESULT,
            cost_usd=Decimal("1.25"),
            num_turns=4,
            session_id="sess-2",
        )
    )

    # Act
    snap = monitor.snapshot()

    # Assert
    assert thinking_snapshot.state == AgentState.THINKING
    assert snap.state == AgentState.COMPLETED
    assert snap.counters.total == 1
    assert snap.counters.running == 0
    assert snap.counters.succeeded == 1
    assert snap.text_count == 1
    assert snap.subagent_count == 1
    assert snap.last_tool == "Read"
    assert snap.last_tool_detail == "src/planora/core/config.py"
    assert snap.recent_tools[0].friendly_name == "Read file"
    assert snap.cost_usd == Decimal("1.25")
    assert snap.num_turns == 4
    assert snap.session_id == "sess-2"
    assert snap.idle_seconds >= 0


def test_monitor_records_failed_tools_and_recent_tool_limit() -> None:
    # Arrange
    monitor = AgentMonitor("codex", max_recent_tools=1)

    for tool_id in ("tool-1", "tool-2"):
        monitor.update(
            StreamEvent(
                event_type=StreamEventType.TOOL_START,
                tool_name="Bash",
                tool_id=tool_id,
                tool_status="running",
            )
        )
        monitor.update(
            StreamEvent(
                event_type=StreamEventType.TOOL_DONE,
                tool_name="Bash",
                tool_id=tool_id,
                tool_status="error" if tool_id == "tool-2" else "done",
            )
        )

    # Act
    snap = monitor.snapshot()

    # Assert
    assert snap.counters.total == 2
    assert snap.counters.succeeded == 1
    assert snap.counters.failed == 1
    assert len(snap.recent_tools) == 1
    assert snap.recent_tools[0].tool_id == "tool-2"


def _monitor_in_state(state: AgentState) -> AgentMonitor:
    monitor = AgentMonitor("agent")

    if state == AgentState.STARTING:
        return monitor
    if state == AgentState.THINKING:
        monitor.update(
            StreamEvent(
                event_type=StreamEventType.STATE_CHANGE,
                text_preview="thinking",
            )
        )
        return monitor
    if state == AgentState.WRITING:
        monitor.update(
            StreamEvent(
                event_type=StreamEventType.STATE_CHANGE,
                text_preview="writing",
            )
        )
        return monitor
    if state == AgentState.TOOL_RUNNING:
        monitor.update(
            StreamEvent(
                event_type=StreamEventType.TOOL_START,
                tool_name="Read",
                tool_id="tool-1",
                tool_status="running",
            )
        )
        return monitor
    if state == AgentState.STALLED:
        monitor.update(StreamEvent(event_type=StreamEventType.STALL, idle_seconds=10.0))
        return monitor
    if state == AgentState.RATE_LIMITED:
        monitor.update(StreamEvent(event_type=StreamEventType.RATE_LIMIT))
        return monitor
    raise AssertionError(f"Unsupported test state: {state}")


@pytest.mark.parametrize(
    "initial_state",
    [
        pytest.param(AgentState.STARTING, id="starting"),
        pytest.param(AgentState.THINKING, id="thinking"),
        pytest.param(AgentState.TOOL_RUNNING, id="tool-running"),
    ],
)
def test_monitor_error_event_transitions_to_failed(initial_state: AgentState) -> None:
    # Arrange
    monitor = _monitor_in_state(initial_state)

    # Act
    monitor.update(StreamEvent(event_type=StreamEventType.ERROR, error="boom"))

    # Assert
    assert monitor.snapshot().state == AgentState.FAILED


@pytest.mark.parametrize(
    "initial_state",
    [
        pytest.param(AgentState.STARTING, id="starting"),
        pytest.param(AgentState.THINKING, id="thinking"),
        pytest.param(AgentState.TOOL_RUNNING, id="tool-running"),
    ],
)
def test_monitor_result_with_error_transitions_to_failed(initial_state: AgentState) -> None:
    # Arrange
    monitor = _monitor_in_state(initial_state)

    # Act
    monitor.update(
        StreamEvent(
            event_type=StreamEventType.RESULT,
            error="non-zero exit",
            exit_code=1,
        )
    )

    # Assert
    assert monitor.snapshot().state == AgentState.FAILED


@pytest.mark.parametrize(
    "initial_state",
    [
        pytest.param(AgentState.STARTING, id="starting"),
        pytest.param(AgentState.THINKING, id="thinking"),
        pytest.param(AgentState.WRITING, id="writing"),
        pytest.param(AgentState.TOOL_RUNNING, id="tool-running"),
        pytest.param(AgentState.STALLED, id="stalled"),
        pytest.param(AgentState.RATE_LIMITED, id="rate-limited"),
    ],
)
def test_monitor_cancel_transitions_non_terminal_states_to_cancelled(
    initial_state: AgentState,
) -> None:
    # Arrange
    monitor = _monitor_in_state(initial_state)

    # Act
    monitor.cancel()

    # Assert
    assert monitor.snapshot().state == AgentState.CANCELLED


def test_monitor_logs_unknown_state_change_hint(caplog: pytest.LogCaptureFixture) -> None:
    # Arrange
    monitor = AgentMonitor("claude")

    # Act
    with caplog.at_level("WARNING"):
        monitor.update(StreamEvent(event_type=StreamEventType.STATE_CHANGE, text_preview="paused"))

    # Assert
    assert monitor.snapshot().state == AgentState.STARTING
    assert "Unknown state change hint" in caplog.text


@pytest.mark.asyncio
async def test_monitor_consume_updates_state_and_emits_callback() -> None:
    # Arrange
    events = [
        StreamEvent(event_type=StreamEventType.INIT, session_id="sess-1"),
        StreamEvent(event_type=StreamEventType.TEXT, text_preview="hello"),
    ]
    seen: list[StreamEventType] = []

    async def raw_events():
        for event in events:
            yield event

    monitor = AgentMonitor("copilot")

    # Act
    await monitor.consume(raw_events(), on_event=lambda event: seen.append(event.event_type))

    # Assert
    snap = monitor.snapshot()
    assert seen == [StreamEventType.INIT, StreamEventType.TEXT]
    assert snap.state == AgentState.WRITING
    assert snap.text_count == 1
