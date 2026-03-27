from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

RUNNER_HARNESS = textwrap.dedent(
    """
    from __future__ import annotations

    import asyncio
    import json
    import sys
    from pathlib import Path

    from orchcore.registry.agent import AgentConfig, AgentMode, OutputExtraction
    from orchcore.runner.subprocess import AgentRunner
    from orchcore.stream.events import StreamFormat
    from orchcore.stream.monitor import AgentMonitor


    async def main() -> None:
        fixture_path = Path(sys.argv[1])
        output_path = Path(sys.argv[2])
        events = []

        agent = AgentConfig(
            name="claude-integration-agent",
            binary=str(fixture_path),
            subcommand="stream",
            model="claude-sonnet-test",
            flags={AgentMode.PLAN: []},
            stream_format=StreamFormat.CLAUDE,
            output_extraction=OutputExtraction(
                strategy=OutputExtraction.Strategy.JQ_FILTER,
            ),
        )

        result = await AgentRunner().run(
            agent,
            "run integration fixture",
            output_path,
            mode=AgentMode.PLAN,
            on_event=events.append,
        )

        monitor = AgentMonitor(agent.name)
        for event in events:
            monitor.update(event)
        snapshot = monitor.snapshot()

        payload = {
            "events": [
                {
                    "event_type": event.event_type.value,
                    "tool_name": event.tool_name,
                    "tool_id": event.tool_id,
                    "tool_detail": event.tool_detail,
                    "tool_status": event.tool_status,
                    "text_preview": event.text_preview,
                }
                for event in events
            ],
            "result": {
                "agent_name": result.agent_name,
                "output_path": str(result.output_path),
                "stream_path": str(result.stream_path),
                "log_path": str(result.log_path),
                "exit_code": result.exit_code,
                "duration_seconds": result.duration.total_seconds() if result.duration else None,
                "cost_usd": str(result.cost_usd) if result.cost_usd is not None else None,
                "token_usage": result.token_usage,
                "num_turns": result.num_turns,
                "session_id": result.session_id,
                "output_empty": result.output_empty,
                "error": result.error,
            },
            "snapshot": {
                "state": snapshot.state.value,
                "counters": snapshot.counters.model_dump(),
                "last_tool": snapshot.last_tool,
                "last_tool_detail": snapshot.last_tool_detail,
                "cost_usd": str(snapshot.cost_usd) if snapshot.cost_usd is not None else None,
                "token_usage": snapshot.token_usage,
                "text_count": snapshot.text_count,
                "subagent_count": snapshot.subagent_count,
                "session_id": snapshot.session_id,
                "num_turns": snapshot.num_turns,
            },
        }
        print(json.dumps(payload))


    asyncio.run(main())
    """
).strip()

EXPECTED_STREAM_OBJECTS = [
    {"type": "system", "subtype": "init", "session_id": "claude-session-1"},
    {"type": "message_start"},
    {"type": "content_block_start", "content_block": {"type": "thinking"}},
    {"type": "content_block_stop"},
    {
        "type": "content_block_start",
        "content_block": {
            "type": "tool_use",
            "name": "Read",
            "id": "tool-1",
            "input": {"file_path": "src/demo.py"},
        },
    },
    {
        "type": "content_block_delta",
        "delta": {
            "type": "input_json_delta",
            "partial_json": '{"file_path":"src/demo.py"}',
        },
    },
    {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "id": "tool-1",
                    "input": {"file_path": "src/demo.py"},
                },
                {
                    "type": "text",
                    "text": "# Claude Output\n\nIntegration succeeded.\n",
                },
            ]
        },
    },
    {"type": "message_stop"},
    {
        "type": "result",
        "total_cost_usd": "0.1234",
        "duration_ms": 42,
        "exit_code": 0,
        "num_turns": 2,
        "session_id": "claude-session-1",
        "usage": {"input_tokens": 12, "output_tokens": 34},
    },
]


def test_agent_runner_processes_claude_stream_end_to_end(tmp_path: Path) -> None:
    # Arrange
    fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "mock_claude.sh"
    output_path = tmp_path / "agent-output.md"

    # Act
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", RUNNER_HARNESS, str(fixture_path), str(output_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(completed.stdout)

    # Assert
    result = summary["result"]
    assert result["agent_name"] == "claude-integration-agent"
    assert result["output_path"] == str(output_path)
    assert result["stream_path"] == str(output_path.with_suffix(".stream"))
    assert result["log_path"] == str(output_path.with_suffix(".log"))
    assert result["exit_code"] == 0
    assert result["duration_seconds"] is not None
    assert result["cost_usd"] == "0.1234"
    assert result["token_usage"] == {"input_tokens": 12, "output_tokens": 34}
    assert result["num_turns"] == 2
    assert result["session_id"] == "claude-session-1"
    assert result["output_empty"] is False
    assert result["error"] is None

    assert output_path.read_text(encoding="utf-8") == "# Claude Output\n\nIntegration succeeded.\n"
    stream_path = output_path.with_suffix(".stream")
    stream_lines = stream_path.read_text(encoding="utf-8").splitlines()
    stream_objects = [json.loads(line) for line in stream_lines]
    assert stream_objects == EXPECTED_STREAM_OBJECTS
    assert output_path.with_suffix(".log").read_text(encoding="utf-8") == ""

    assert [event["event_type"] for event in summary["events"]] == [
        "init",
        "state",
        "tool_start",
        "tool_exec",
        "tool_done",
        "text",
        "result",
    ]

    tool_start = summary["events"][2]
    assert tool_start["tool_name"] == "Read"
    assert tool_start["tool_id"] == "tool-1"
    assert tool_start["tool_detail"] == "src/demo.py"
    assert tool_start["tool_status"] == "running"

    tool_done = summary["events"][4]
    assert tool_done["tool_name"] == "Read"
    assert tool_done["tool_id"] == "tool-1"
    assert tool_done["tool_detail"] == "src/demo.py"
    assert tool_done["tool_status"] == "done"

    text_event = summary["events"][5]
    assert text_event["text_preview"] == "# Claude Output\n\nIntegration succeeded.\n"

    snapshot = summary["snapshot"]
    assert snapshot["state"] == "completed"
    assert snapshot["counters"] == {
        "total": 1,
        "succeeded": 1,
        "failed": 0,
        "running": 0,
    }
    assert snapshot["last_tool"] == "Read"
    assert snapshot["last_tool_detail"] == "src/demo.py"
    assert snapshot["cost_usd"] == "0.1234"
    assert snapshot["token_usage"] == {"input_tokens": 12, "output_tokens": 34}
    assert snapshot["text_count"] == 1
    assert snapshot["subagent_count"] == 0
    assert snapshot["session_id"] == "claude-session-1"
    assert snapshot["num_turns"] == 2
